# -*- coding: utf-8 -*-
"""实跑产物验收：点金术名单逐条复核规则 + 同花顺实时对照 + 自选股产物完整性。

用法（在仓库根目录）：
    python tools/validate_outputs.py

检查内容：
1. 最新 data/dianjin/<日期>/ 的点金术名单：股息>3（extra>4）、三档 0<PE<20、
   收盘/MA120<0.88（extra<0.82）、名称非空、extra ⊆ 点金术。
2. 指标日新鲜度：应统一为最近一个完整交易日；不一致的逐只列出（可能为停牌）。
3. 同花顺对照：抽样命中股 + 锚点中油资本 000617，现场拉 F10 分红表复算
   股息率TTM。用名单收盘价做分子还原（DPS = 名单股息率/100×收盘），
   与现场 DPS/收盘 比对（±0.06 个百分点）。跨日价格波动不单独当错误。
4. data/watchlist/ 自选股产物：总表、每只个股的 综合分析.md、技术面/基本面文件，
   以及 日线指标.csv 最后一行日期是否同样新鲜。

只读校验，不改任何输出。退出码 0=全部通过，1=有失败项。
"""

from __future__ import annotations

import csv
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dianjin.rules import to_float  # noqa: E402

SAMPLE_N = 10          # 同花顺现场对照抽样数
YIELD_TOL = 0.06       # 名单值与现场复算的容差（百分点）
ANCHOR = ("000617", 1.50, 6.79, 0.08)  # 中油资本：参考日股息率、参考收盘、复算容差

failures: list[str] = []
notes: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)
    print(f"  [FAIL] {msg}")


def note(msg: str) -> None:
    notes.append(msg)
    print(f"  [note] {msg}")


def ok(msg: str) -> None:
    print(f"  [ok] {msg}")


def expected_last_session(today: date) -> date:
    """最近一个应有完整日 K 的交易日（不考虑节假日表：周一~周五取前一工作日）。"""
    day = today - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def latest_dianjin_dir() -> Path | None:
    base = ROOT / "data" / "dianjin"
    if not base.is_dir():
        return None
    dated = sorted(d for d in base.iterdir() if d.is_dir() and d.name.isdigit())
    return dated[-1] if dated else None


def read_list_csv(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def check_dianjin_rules(rows: list[dict], *, extra: bool, label: str) -> None:
    # CSV 是显示值：股息/PE 保留 2 位、比值保留 4 位。筛选用的是全精度原值，
    # 边界判断放宽半个显示单位，避免 4.0027 显示成 4.00 被误报。
    div_min = 4.0 if extra else 3.0
    ratio_max = 0.82 if extra else 0.88
    half_2dp, half_4dp = 0.005, 0.00005
    for row in rows:
        code = str(row.get("代码") or "").zfill(6)
        name = str(row.get("名称") or "").strip()
        if not name or name == code:
            fail(f"{label} {code} 名称为空")
        dividend = to_float(row.get("股息率%"))
        if dividend is None or dividend <= div_min - half_2dp:
            fail(f"{label} {code} {name} 股息率 {row.get('股息率%')} 不满足 >{div_min}")
        for col in ("PE动态", "PE静态", "PE_TTM"):
            pe = to_float(row.get(col))
            if pe is None or pe <= 0 or pe >= 20 + half_2dp:
                fail(f"{label} {code} {name} {col}={row.get(col)} 不在 (0,20)")
        ratio = to_float(row.get("收盘/MA120"))
        if ratio is None or ratio >= ratio_max + half_4dp:
            fail(f"{label} {code} {name} 收盘/MA120={row.get('收盘/MA120')} 不满足 <{ratio_max}")
    ok(f"{label} 共 {len(rows)} 只，规则逐条复核完成（边界按显示精度放宽半个单位）")


def check_indicator_dates(rows: list[dict], label: str, expected: date) -> None:
    stale: list[str] = []
    for row in rows:
        raw = str(row.get("指标日") or "").strip()
        if raw != expected.isoformat():
            stale.append(f"{row.get('代码')} {row.get('名称')} 指标日={raw or '空'}")
    if stale:
        note(f"{label} 指标日非 {expected}（共 {len(stale)} 只，可能停牌/源延迟）：" + "；".join(stale))
    else:
        ok(f"{label} 全部指标日 = {expected}")


def check_ths_yields(rows: list[dict]) -> None:
    from dianjin.ths_yield import fetch_ths_bonus_payouts, ths_ttm_yield_percent

    today = date.today()
    sample = rows[:SAMPLE_N]
    for row in sample:
        code = str(row.get("代码") or "").zfill(6)
        listed = to_float(row.get("股息率%"))
        close = to_float(row.get("收盘"))
        payouts, fetched = fetch_ths_bonus_payouts(code)
        if not fetched:
            note(f"{code} 同花顺分红页拉取失败，跳过对照")
            continue
        live = ths_ttm_yield_percent(payouts, close, as_of=today)
        if live is None or listed is None:
            fail(f"{code} 对照失败：名单 {listed} vs 现算 {live}")
            continue
        if abs(live - listed) > YIELD_TOL:
            fail(f"{code} 股息率不一致：名单 {listed:.2f} vs 同花顺现算 {live:.2f}")
        else:
            ok(f"{code} 股息率 {listed:.2f} ≈ 同花顺现算 {live:.2f}")


def check_anchor() -> None:
    from dianjin.ths_yield import fetch_ths_bonus_payouts, ths_ttm_yield_percent
    from dianjin.yield_quote import fetch_tencent_quotes

    code, ref_yield, ref_close, tol = ANCHOR
    quotes = fetch_tencent_quotes([code])
    fields = quotes.get(code) or []
    price = to_float(fields[3]) if len(fields) > 3 else None
    if price is None:
        note(f"锚点 {code} 腾讯现价拉取失败，跳过")
        return
    payouts, fetched = fetch_ths_bonus_payouts(code)
    if not fetched:
        note(f"锚点 {code} 同花顺分红页拉取失败，跳过")
        return
    live = ths_ttm_yield_percent(payouts, price, as_of=date.today())
    expected = ref_yield * ref_close / price
    if live is None or abs(live - expected) > tol:
        fail(
            f"锚点 中油资本 {code}：现算 {live} 偏离价格调整期望 {expected:.2f}±{tol}"
            f"（现价 {price}，参考 {ref_yield}%@{ref_close}）"
        )
    else:
        ok(
            f"锚点 中油资本 {code}：现算 {live:.2f} ≈ 价格调整期望 {expected:.2f}"
            f"（现价 {price}，DPS 对齐 {ref_yield}%@{ref_close}）"
        )


def check_watchlist_outputs(expected: date) -> None:
    from tech_analysis.watchlist import load_watchlist

    wl = ROOT / "userdata" / "watchlist.txt"
    items = load_watchlist(wl) if wl.is_file() else []
    root = ROOT / "data" / "watchlist"
    if not root.is_dir():
        fail(f"缺少自选股输出目录 {root}")
        return
    for fname in ("总表.md", "总表.csv", "技术面汇总.md", "基本面汇总.md"):
        if not (root / fname).is_file():
            fail(f"自选股缺少 {fname}")
    stocks_dir = root / "个股"
    folders = {p.name[:6]: p for p in stocks_dir.iterdir() if p.is_dir()} if stocks_dir.is_dir() else {}
    missing = [it.code for it in items if it.code not in folders]
    if missing:
        fail(f"自选股缺少个股目录：{'、'.join(missing)}")
    else:
        ok(f"自选股 {len(items)} 只个股目录齐全")
    stale: list[str] = []
    for code, folder in sorted(folders.items()):
        if "_" not in folder.name or folder.name.endswith("_"):
            fail(f"{code} 个股目录名缺名称：{folder.name}")
        if not (folder / "综合分析.md").is_file():
            fail(f"{code} 缺少 综合分析.md")
        daily_csv = folder / "技术面" / "日线指标.csv"
        if not daily_csv.is_file():
            fail(f"{code} 缺少 技术面/日线指标.csv")
            continue
        with daily_csv.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        last = str(rows[-1].get("date") or rows[-1].get("日期") or "")[:10] if rows else ""
        if last != expected.isoformat():
            stale.append(f"{code} 最后交易日={last or '空'}")
    if stale:
        note(f"自选股日线非 {expected}（共 {len(stale)} 只，可能停牌）：" + "；".join(stale))
    else:
        ok(f"自选股全部日线最后一根 = {expected}")


def main() -> int:
    expected = expected_last_session(date.today())
    print(f"== 期望最近完整交易日：{expected} ==")

    dj_root = latest_dianjin_dir()
    if dj_root is None:
        fail("找不到 data/dianjin/<日期>/")
    else:
        print(f"== 点金术目录：{dj_root} ==")
        main_rows = read_list_csv(dj_root / "点金术" / "点金术.csv")
        extra_rows = read_list_csv(dj_root / "点金术extra" / "点金术extra.csv")
        if not main_rows:
            note("点金术名单为空（今日无符合）")
        else:
            check_dianjin_rules(main_rows, extra=False, label="点金术")
            check_dianjin_rules(extra_rows, extra=True, label="extra")
            main_codes = {str(r.get("代码")).zfill(6) for r in main_rows}
            outside = [str(r.get("代码")).zfill(6) for r in extra_rows
                       if str(r.get("代码")).zfill(6) not in main_codes]
            if outside:
                fail(f"extra 不是子集，多出：{'、'.join(outside)}")
            else:
                ok(f"extra ⊆ 点金术（{len(extra_rows)}/{len(main_rows)}）")
            check_indicator_dates(main_rows, "点金术", expected)
            for fname in ("总表.md", "技术信号汇总.md", "技术信号汇总.csv"):
                if not (dj_root / "点金术" / fname).is_file():
                    fail(f"点金术缺少 {fname}")
            print("== 同花顺股息率现场对照 ==")
            check_ths_yields(main_rows)

    print("== 锚点核对 ==")
    check_anchor()

    print("== 自选股产物 ==")
    check_watchlist_outputs(expected)

    print()
    if failures:
        print(f"验收失败 {len(failures)} 项；提示 {len(notes)} 条")
        return 1
    print(f"验收通过；提示 {len(notes)} 条（见上方 note）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
