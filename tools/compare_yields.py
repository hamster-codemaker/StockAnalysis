# -*- coding: utf-8 -*-
"""对照点金术筛选用股息率（f133 / 同花顺TTM 同列）与同花顺 F10 分红表计算值。

不做 PE / MA120 / 3% 预过滤。锚点：中油资本 1.50、思维列控 2.43。
腾讯[64] 只作反例，不当官方。

  python tools/compare_yields.py --limit 200
  python tools/compare_yields.py --cached --limit 80
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dianjin.em_clist import fetch_clist_all
from dianjin.rules import is_bse, to_float
from dianjin.ths_yield import fetch_ths_ttm_map
from dianjin.yield_quote import (
    EM_NOT_YIELD_FIELD,
    EM_SCREEN_YIELD_FIELD,
    parse_yield_percent,
    screen_yield_from_clist,
)

COMPARE_FIELDS = f"f2,f12,f14,{EM_SCREEN_YIELD_FIELD},{EM_NOT_YIELD_FIELD}"
CNPC = "000617"
THINKER = "603508"
MUST = (CNPC, THINKER)
# 旧对照里被腾讯年度口径带偏的名字，必须用同花顺TTM 复核
MUST_ALSO = (
    "603569",
    "000568",
    "601928",
    "600690",
    "600197",
    "600352",
    "600050",
    "600956",
)


def cache_dir(stamp: str | None = None) -> Path:
    day = stamp or date.today().strftime("%Y%m%d")
    dest = ROOT / "data" / "cache" / "yield_compare" / day
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _code(raw: Any) -> str:
    return str(raw or "").strip().zfill(6)


def rows_from_clist(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_rows:
        code = _code(raw.get("f12"))
        if not code.isdigit() or len(code) != 6 or code in seen:
            continue
        seen.add(code)
        out.append(
            {
                "code": code,
                "name": str(raw.get("f14") or "").strip(),
                "close": to_float(raw.get("f2")),
                "ours": screen_yield_from_clist(raw),
                "f183": to_float(raw.get(EM_NOT_YIELD_FIELD)),
            }
        )
    return out


def attach_ths(rows: list[dict[str, Any]], ths_map: dict[str, float | None]) -> list[dict[str, Any]]:
    for row in rows:
        official = parse_yield_percent(ths_map.get(row["code"]))
        row["official"] = official
        ours = row.get("ours")
        if ours is not None and official is not None:
            row["diff"] = ours - official
            row["abs_diff"] = abs(row["diff"])
        else:
            row["diff"] = None
            row["abs_diff"] = None
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    compared = [r for r in rows if r.get("abs_diff") is not None]
    abs_err = [float(r["abs_diff"]) for r in compared]
    n = len(abs_err)

    def band(limit: float) -> float | None:
        if not n:
            return None
        return sum(1 for x in abs_err if x <= limit) / n * 100.0

    worst = sorted(compared, key=lambda r: (-float(r["abs_diff"]), r["code"]))[:10]
    cnpc = next((r for r in rows if r.get("code") == CNPC), None)
    thinker = next((r for r in rows if r.get("code") == THINKER), None)
    return {
        "universe": len(rows),
        "n": n,
        "ours_only": sum(1 for r in rows if r.get("ours") is not None and r.get("official") is None),
        "official_only": sum(1 for r in rows if r.get("ours") is None and r.get("official") is not None),
        "median_abs": statistics.median(abs_err) if abs_err else None,
        "mean_abs": statistics.mean(abs_err) if abs_err else None,
        "pct_within_0_05": band(0.05),
        "pct_within_0_10": band(0.10),
        "pct_within_0_20": band(0.20),
        "worst": [
            {
                "code": r["code"],
                "name": r["name"],
                "ours": r.get("ours"),
                "official": r.get("official"),
                "diff": r.get("diff"),
                "f183": r.get("f183"),
            }
            for r in worst
        ],
        "cnpc": cnpc,
        "thinker": thinker,
    }


def format_report(stats: dict[str, Any], *, stamp: str, source: str) -> str:
    cnpc = stats.get("cnpc") or {}
    thinker = stats.get("thinker") or {}

    def fmt(value: Any, digits: int = 4) -> str:
        try:
            return f"{float(value):.{digits}f}"
        except (TypeError, ValueError):
            return "—"

    lines = [
        f"# 股息率对照 {stamp}（同花顺股息率TTM）",
        "",
        f"- 筛选用：东财 clist `{EM_SCREEN_YIELD_FIELD}`（与同花顺「股息率TTM」同列）",
        "- 官方对照：同花顺 F10 bonus.html 已实施派现 / 现价（剔除股利支付率>200% 特别分红；含即将除权）",
        "- 腾讯[64] **不是**官方（思维列控会变成 12.18 年度口径）",
        f"- 样本来源：{source}",
        f"- 对照只数 n={stats.get('n')}（收录 {stats.get('universe')}；仅快照有数 {stats.get('ours_only')}；仅同花顺有数 {stats.get('official_only')}）",
        f"- 绝对误差 中位数 {fmt(stats.get('median_abs'))}  均值 {fmt(stats.get('mean_abs'))}",
        f"- 误差 ≤0.05：{fmt(stats.get('pct_within_0_05'), 2)}%   ≤0.10：{fmt(stats.get('pct_within_0_10'), 2)}%   ≤0.20：{fmt(stats.get('pct_within_0_20'), 2)}%",
        "",
        "## 中油资本 000617（目标 1.50）",
        "",
    ]
    if cnpc:
        lines += [
            f"- 名称：{cnpc.get('name') or '中油资本'}",
            f"- 快照 f133：{fmt(cnpc.get('ours'), 2)}",
            f"- 同花顺TTM：{fmt(cnpc.get('official'), 2)}",
            f"- 误用 f183：{fmt(cnpc.get('f183'), 2)}",
            "",
        ]
    else:
        lines += ["- **未入样**", ""]
    lines += ["## 思维列控 603508（目标 2.43，不是 12.18）", ""]
    if thinker:
        lines += [
            f"- 名称：{thinker.get('name') or '思维列控'}",
            f"- 快照 f133：{fmt(thinker.get('ours'), 2)}",
            f"- 同花顺TTM：{fmt(thinker.get('official'), 2)}",
            "",
        ]
    else:
        lines += ["- **未入样**", ""]
    lines += [
        "## 误差最大 10 只",
        "",
        "| 代码 | 名称 | 快照 | 同花顺TTM | 差 | f183 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in stats.get("worst") or []:
        lines.append(
            f"| {row.get('code')} | {row.get('name')} | {fmt(row.get('ours'), 2)} | "
            f"{fmt(row.get('official'), 2)} | {fmt(row.get('diff'), 2)} | {fmt(row.get('f183'), 2)} |"
        )
    lines.append("")
    return "\n".join(lines)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _select_rows(rows: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    by_code = {r["code"]: r for r in rows}
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()

    def take(code: str) -> None:
        if code in seen:
            return
        row = by_code.get(code)
        if row is None:
            row = {"code": code, "name": "", "close": None, "ours": None, "f183": None}
        picked.append(row)
        seen.add(code)

    for code in (*MUST, *MUST_ALSO):
        take(code)
    # 沪深 A 股；北交所 92/4/8 没有同花顺这套 TTM 栏目，不占样本
    for row in rows:
        code = row["code"]
        if code in seen or is_bse(code):
            continue
        picked.append(row)
        seen.add(code)
        if limit and len(picked) >= int(limit):
            break
    return picked


def run_compare(
    *,
    limit: int | None = 200,
    cached: bool = False,
    stamp: str | None = None,
    snapshot_rows: list[dict[str, Any]] | None = None,
    ths_map: dict[str, float | None] | None = None,
    as_of: date | None = None,
) -> tuple[dict[str, Any], str, Path]:
    dest = cache_dir(stamp)
    clist_path = dest / "clist.json"
    ths_path = dest / "ths_ttm.json"
    source = "live"
    day = as_of or date.today()

    if snapshot_rows is None:
        if cached and clist_path.is_file():
            snapshot_rows = _load_json(clist_path)
            source = "cached clist"
        else:
            snapshot_rows = fetch_clist_all(
                COMPARE_FIELDS,
                page_size=100,
                sleep_seconds=0.15,
                timeout=12.0,
                min_rows=1000,
                log_prefix="股息率对照快照",
            )
            _dump_json(clist_path, snapshot_rows)
            source = "live clist"
    rows = _select_rows(rows_from_clist(snapshot_rows), limit)

    if ths_map is None:
        if cached and ths_path.is_file():
            raw = _load_json(ths_path)
            ths_map = {str(k).zfill(6): parse_yield_percent(v) for k, v in raw.items()}
            source += " + cached 同花顺TTM"
        else:
            ths_map = fetch_ths_ttm_map(rows, as_of=day)
            _dump_json(ths_path, ths_map)
            source += " + live 同花顺 F10"

    attach_ths(rows, ths_map)
    stats = summarize(rows)
    report = format_report(stats, stamp=dest.name, source=source)
    (dest / "report.md").write_text(report, encoding="utf-8")
    _dump_json(dest / "report.json", {"stats": stats, "source": source})
    return stats, report, dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="对照 f133 与同花顺股息率TTM")
    parser.add_argument("--limit", type=int, default=200, help="对照只数（含两只锚点）。0=快照全部（很慢）")
    parser.add_argument("--cached", action="store_true", help="只用已落盘 clist / ths_ttm")
    parser.add_argument("--stamp", default="", help="缓存日期 YYYYMMDD")
    args = parser.parse_args(argv)
    limit = args.limit if args.limit > 0 else None
    stats, report, dest = run_compare(limit=limit, cached=args.cached, stamp=args.stamp or None)
    print(report)
    print(f"已写入 {dest}")
    cnpc = stats.get("cnpc") or {}
    thinker = stats.get("thinker") or {}
    official_c = parse_yield_percent(cnpc.get("official"))
    official_t = parse_yield_percent(thinker.get("official"))
    ours_c = parse_yield_percent(cnpc.get("ours"))
    ours_t = parse_yield_percent(thinker.get("ours"))
    rc = 0
    if ours_c is None or official_c is None or abs((official_c or 0) - 1.50) > 0.08:
        print("警告：中油资本未对齐同花顺 1.50", file=sys.stderr)
        rc = 2
    if ours_t is None or official_t is None or abs((official_t or 0) - 2.43) > 0.08:
        print("警告：思维列控未对齐同花顺 2.43", file=sys.stderr)
        rc = 2
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
