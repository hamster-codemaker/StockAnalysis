"""点金术总表 + 名单 + 空列表特判。"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

from dianjin.mdfmt import fmt_num as _fmt
from dianjin.mdfmt import md_table as _md_table
from dianjin.screen import DianjinHit, ScreenResult
from dianjin.signal_summary import (
    EXTRA_SUMMARY_CSV,
    EXTRA_SUMMARY_MD,
    MAIN_SUMMARY_CSV,
    MAIN_SUMMARY_MD,
    write_bundle_signal_summaries,
)
from dianjin.tech_summary import TECH_COLUMNS, cells_from_enrich

log = logging.getLogger("dianjin")

EMPTY_MARK = "今日无符合"
WATCHLIST_BADGE = "自选"

LIST_COLUMNS = [
    "代码",
    "名称",
    "自选",
    "收盘",
    "MA120",
    "收盘/MA120",
    "股息率%",
    "PE动态",
    "PE静态",
    "PE_TTM",
    "extra",
    *TECH_COLUMNS,
    "近5日信号",
    "个股报告",
]


MAIN_FOLDER = "点金术"
EXTRA_FOLDER = "点金术extra"


def main_dir(root: Path) -> Path:
    return Path(root) / MAIN_FOLDER


def extra_dir(root: Path) -> Path:
    return Path(root) / EXTRA_FOLDER


def _hit_link(folder: str, screen_only: bool) -> str:
    if screen_only:
        return "—"
    return f"[个股报告](个股/{folder}/个股报告.md)"


def _marked_watchlist(hit: DianjinHit, info: dict | None, watchlist_codes: set[str] | None) -> bool:
    if info and "in_watchlist" in info:
        return bool(info.get("in_watchlist"))
    if not watchlist_codes:
        return False
    return str(hit.code).zfill(6) in watchlist_codes


def _row_cells(
    hit: DianjinHit,
    info: dict | None,
    screen_only: bool,
    watchlist_codes: set[str] | None = None,
) -> list[str]:
    from dianjin.watchlist_mark import stock_folder_name

    marked = _marked_watchlist(hit, info, watchlist_codes)
    folder = (info or {}).get("folder") or stock_folder_name(hit.code, hit.name, marked)
    recent = (info or {}).get("recent_signals") or ("—" if screen_only else "无")
    tech = cells_from_enrich(info)
    return [
        hit.code,
        hit.name,
        WATCHLIST_BADGE if marked else "",
        _fmt(hit.close, 3),
        _fmt(hit.ma120, 3),
        _fmt(hit.close_ma_ratio, 4),
        _fmt(hit.dividend, 2),
        _fmt(hit.pe_dyn, 2),
        _fmt(hit.pe_static, 2),
        _fmt(hit.pe_ttm, 2),
        "是" if hit.is_extra else "否",
        *[tech[key] for key in TECH_COLUMNS],
        str(recent),
        _hit_link(folder, screen_only),
    ]


def _csv_dict(
    hit: DianjinHit,
    info: dict | None,
    screen_only: bool,
    watchlist_codes: set[str] | None = None,
) -> dict:
    cells = _row_cells(hit, info, screen_only, watchlist_codes)
    return dict(zip(LIST_COLUMNS, cells))


def _rules_block() -> list[str]:
    return [
        "筛选规则（严格不等式；缺数/非数字/负 PE 一律不通过）：",
        "- 点金术：股息率 **> 3%**（**同花顺股息率TTM**，来自 F10 分红表；"
        "快照先读东财 **f133** 同列，再对已过三档 PE 的股票用同花顺 bonus.html 覆盖。"
        "锚点：思维列控 2.43、中油资本 1.50。剔除股利支付率>200% 的特别分红。"
        "**不用** f183，**不用**同花顺 526792 振幅，也**不用**腾讯字段 64——"
        "后者常是年度/含特别分红，思维列控会变成 12.18）。"
        "市盈率动态、静态、TTM 均满足 **0 < PE < 20**；"
        "当日收盘（前复权 K 线）**< MA120 × 88%**。",
        "- 点金术extra：同一套市盈率，且同一股息率 **> 4%**、收盘 **< MA120 × 82%**。"
        " extra 是点金术的加严子集（extra ⊆ 点金术）。",
        "- 剔除 ST / *ST / 退市整理股；默认不含北交所。与自选股 `userdata/watchlist.txt`、"
        "成长股 `data/screening/` **分开存放**。筛选**不读**自选股名单；"
        "若命中股也在自选股中，个股目录加 `_自选` 后缀，总表「自选」列打标（例如 `600900_长江电力_自选`）。"
        "清理仍按 6 位代码匹配，不依赖完整文件夹名。",
        "- 个股图：最近 **120 个交易日** K 线（不是日历日），叠加 MA8 / MA24 / MA120，"
        "分图 BOLL(20,2)、MACD(12,26,9)、RSI 通达信 RSI1/2/3=6/12/24；高亮近 5 个交易日信号。"
        "为让 MA120 在可见窗口内有定义，日线预热约 250 根。"
        "总表「指标日 / MA8 / MA24 / BOLL / MACD / RSI」复用命中股已算的日线，不另拉行情。",
        "- 生产路径：全市场数据中心估值快照（静态/TTM PE+预填股息，动态 PE 用腾讯下标 52 补齐）→ 先过滤股息+PE → "
        "**只对幸存者**用腾讯前复权日线算 MA120；"
        "缺均线/财务再补一轮；技术面与财务只增强最终命中股。",
        "- 财务：复用本地 `data/docs`；若从未下载，当日拉公开财报接口 + **最新一期**定期 PDF，"
        "不下历史招股书/全量财报/研报。",
    ]


def _folder_code(name: str) -> str | None:
    stem = str(name or "").strip()
    if not stem:
        return None
    head = stem.split("_", 1)[0]
    if head.isdigit() and len(head) == 6:
        return head.zfill(6)
    if stem.isdigit() and len(stem) == 6:
        return stem.zfill(6)
    return None


def prune_stale_stock_dirs(
    out_dir: Path,
    keep_codes: list[str] | set[str],
    *,
    label: str = "点金术",
    preferred_names: dict[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    """只保留当日名单对应的 个股/ 目录，删除已出局股票的点金产物。

    按 **6 位代码** 匹配（文件夹可带 `_自选` 后缀），不动 userdata、data/docs。
    空名单时 keep 为空，清空全部个股目录。
    若提供 preferred_names，同一代码的旧目录名在目标目录已存在时会被删掉。
    """
    root = Path(out_dir) / "个股"
    keep = {str(c).zfill(6) for c in keep_codes if str(c).strip()}
    preferred = {str(k).zfill(6): v for k, v in (preferred_names or {}).items() if v}
    deleted: list[str] = []
    kept: list[str] = []
    if not root.is_dir():
        log.info("%s个股清理：目录不存在，删除 0，保留 %s", label, "、".join(sorted(keep)) or "无")
        return deleted, sorted(keep)
    for item in sorted(root.iterdir()):
        if item.is_file():
            try:
                item.unlink()
                log.info("已删除%s个股散文件：%s", label, item.name)
            except OSError as exc:
                log.warning("未能删除%s个股散文件 %s：%s", label, item, exc)
            continue
        if not item.is_dir():
            continue
        code = _folder_code(item.name)
        if code is None:
            continue
        if code not in keep:
            shutil.rmtree(item, ignore_errors=True)
            if item.exists():
                log.warning("未能删除%s出局个股目录：%s", label, item)
            else:
                deleted.append(code)
                log.info("已删除%s出局个股：%s（%s）", label, code, item.name)
            continue
        want = preferred.get(code)
        if want and item.name != want and (root / want).is_dir():
            shutil.rmtree(item, ignore_errors=True)
            if not item.exists():
                log.info("已删除%s重复个股目录：%s（保留 %s）", label, item.name, want)
            continue
        kept.append(code)
    log.info(
        "%s个股清理：删除 %s，保留 %s",
        label,
        "、".join(deleted) or "无",
        "、".join(sorted(set(kept) | keep)) or "无",
    )
    return deleted, sorted(set(kept))


def copy_stock_dirs(src_root: Path, dest_root: Path, codes: list[str] | set[str]) -> int:
    """把仍在名单内的个股目录复制到另一份点金文件夹（extra 独立副本）。"""
    src_stocks = Path(src_root) / "个股"
    dest_stocks = Path(dest_root) / "个股"
    dest_stocks.mkdir(parents=True, exist_ok=True)
    wanted = {str(c).zfill(6) for c in codes if str(c).strip()}
    copied = 0
    if not src_stocks.is_dir():
        return 0
    for item in src_stocks.iterdir():
        if not item.is_dir():
            continue
        code = _folder_code(item.name)
        if code is None or code not in wanted:
            continue
        for other in list(dest_stocks.iterdir()):
            if other.is_dir() and _folder_code(other.name) == code and other.name != item.name:
                shutil.rmtree(other, ignore_errors=True)
        dest = dest_stocks / item.name
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(item, dest)
        copied += 1
    return copied


def clear_legacy_flat_outputs(out_dir: Path) -> None:
    """去掉旧版写在日期根下的扁平 总表/名单/个股，避免和独立文件夹混在一起。"""
    root = Path(out_dir)
    for name in ("个股", "点金术.md", "点金术.csv", "点金术extra.md", "点金术extra.csv"):
        path = root / name
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.is_file():
            try:
                path.unlink()
            except OSError:
                pass


def write_empty_reports(out_dir: Path, *, stamp: str, error: str = "") -> None:
    """空名单特判：写齐总表与两份名单，正文明确「今日无符合」，不抛异常。"""
    write_reports(ScreenResult(error=error), out_dir, stamp=stamp, enrich_info=[], screen_only=True)


def write_list_files(
    hits: list[DianjinHit],
    out_dir: Path,
    *,
    title: str,
    stem: str,
    stamp: str,
    error: str,
    info_map: dict[str, dict],
    screen_only: bool,
    watchlist_codes: set[str] | None = None,
) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{stem}.csv"
    md_path = out_dir / f"{stem}.md"
    rows = [_csv_dict(h, info_map.get(h.code), screen_only, watchlist_codes) for h in hits]
    pd.DataFrame(rows, columns=LIST_COLUMNS).to_csv(csv_path, index=False, encoding="utf-8-sig")

    marked_n = sum(1 for h in hits if _marked_watchlist(h, info_map.get(h.code), watchlist_codes))
    lines = [
        f"# {title} {stamp}",
        "",
        f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M}",
        f"- 入选：{len(hits)} 只",
        f"- 其中自选股：{marked_n} 只（仅标注，筛选股票池仍是全市场）",
        "",
        *_rules_block(),
        "",
    ]
    if error:
        lines += ["## 筛选未能完成", "", error, ""]
    if not hits:
        lines += [EMPTY_MARK, ""]
    else:
        lines += [
            _md_table(
                LIST_COLUMNS,
                [_row_cells(h, info_map.get(h.code), screen_only, watchlist_codes) for h in hits],
            ),
            "",
        ]
        if not screen_only:
            lines += ["个股报告在本文件夹的 `个股/` 下，可单独打开。自选股目录名带 `_自选`。", ""]
    md_path.write_text("\n".join(lines), encoding="utf-8")


def _write_bundle_overview(
    out_dir: Path,
    *,
    title: str,
    stamp: str,
    screen: ScreenResult,
    hits: list[DianjinHit],
    info_map: dict[str, dict],
    screen_only: bool,
    list_stem: str,
    sibling_note: str = "",
    watchlist_codes: set[str] | None = None,
    signal_md: str = MAIN_SUMMARY_MD,
    signal_csv: str = MAIN_SUMMARY_CSV,
) -> None:
    marked_n = sum(1 for h in hits if _marked_watchlist(h, info_map.get(h.code), watchlist_codes))
    lines = [
        f"# {title}总表 {stamp}",
        "",
        f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M}",
        f"- 全市场快照：{screen.snapshot_count} 只；剔除 ST/北交所后 {screen.after_universe}；"
        f"股息+PE {screen.value_pass}；已拉 MA120 {screen.ma_fetched}",
        f"- 本名单：{len(hits)} 只；其中自选股 {marked_n} 只（标注，不改筛选池）",
        "",
        *_rules_block(),
        "",
    ]
    if screen.error:
        lines += ["## 筛选未能完成", "", screen.error, ""]
    if not hits:
        lines += [EMPTY_MARK, ""]
    else:
        lines += [
            f"## {title}",
            "",
            _md_table(
                LIST_COLUMNS,
                [_row_cells(h, info_map.get(h.code), screen_only, watchlist_codes) for h in hits],
            ),
            "",
        ]
    lines += [
        f"- [名单]({list_stem}.md) / [CSV]({list_stem}.csv)",
        f"- [技术信号汇总]({signal_md}) / [CSV]({signal_csv})",
        "",
    ]
    if sibling_note:
        lines += [sibling_note, ""]
    (Path(out_dir) / "总表.md").write_text("\n".join(lines), encoding="utf-8")


def write_reports(
    screen: ScreenResult,
    out_dir: Path,
    *,
    stamp: str | None = None,
    enrich_info: list[dict] | None = None,
    extra_enrich_info: list[dict] | None = None,
    screen_only: bool = False,
    watchlist_codes: set[str] | None = None,
) -> Path:
    """写出独立的 点金术/ 与 点金术extra/ 文件夹。空名单写「今日无符合」。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    clear_legacy_flat_outputs(out_dir)
    day = stamp or datetime.now().strftime("%Y%m%d")
    if watchlist_codes is None:
        from dianjin.watchlist_mark import load_watchlist_codes

        watchlist_codes = load_watchlist_codes()
    info_map = {str(item["hit"].code): item for item in (enrich_info or []) if item.get("hit")}
    extra_map = {str(item["hit"].code): item for item in (extra_enrich_info or []) if item.get("hit")}
    if not extra_map:
        extra_map = {code: info for code, info in info_map.items() if info.get("hit") and getattr(info["hit"], "is_extra", False)}
    error = screen.error or ""
    hits = list(screen.hits)
    extra = list(screen.extra)
    main = main_dir(out_dir)
    extra_path = extra_dir(out_dir)
    main.mkdir(parents=True, exist_ok=True)
    extra_path.mkdir(parents=True, exist_ok=True)
    (main / "个股").mkdir(parents=True, exist_ok=True)
    (extra_path / "个股").mkdir(parents=True, exist_ok=True)
    marked_n = sum(1 for h in hits if _marked_watchlist(h, info_map.get(h.code), watchlist_codes))

    write_list_files(
        hits,
        main,
        title="点金术",
        stem="点金术",
        stamp=day,
        error=error,
        info_map=info_map,
        screen_only=screen_only,
        watchlist_codes=watchlist_codes,
    )
    write_list_files(
        extra,
        extra_path,
        title="点金术extra",
        stem="点金术extra",
        stamp=day,
        error=error,
        info_map=extra_map or info_map,
        screen_only=screen_only,
        watchlist_codes=watchlist_codes,
    )
    _write_bundle_overview(
        main,
        title="点金术",
        stamp=day,
        screen=screen,
        hits=hits,
        info_map=info_map,
        screen_only=screen_only,
        list_stem="点金术",
        sibling_note="- extra 子集在并列文件夹 [点金术extra](../点金术extra/总表.md)",
        watchlist_codes=watchlist_codes,
        signal_md=MAIN_SUMMARY_MD,
        signal_csv=MAIN_SUMMARY_CSV,
    )
    _write_bundle_overview(
        extra_path,
        title="点金术extra",
        stamp=day,
        screen=screen,
        hits=extra,
        info_map=extra_map or info_map,
        screen_only=screen_only,
        list_stem="点金术extra",
        sibling_note="- 完整点金术名单在并列文件夹 [点金术](../点金术/总表.md)",
        watchlist_codes=watchlist_codes,
        signal_md=EXTRA_SUMMARY_MD,
        signal_csv=EXTRA_SUMMARY_CSV,
    )
    write_bundle_signal_summaries(
        hits,
        extra,
        main,
        extra_path,
        stamp=day,
        info_map=info_map,
        extra_map=extra_map,
    )
    index = [
        f"# 点金术 {day}",
        "",
        f"- [点金术]({MAIN_FOLDER}/总表.md)（{len(hits)} 只，其中自选 {marked_n} 只）",
        f"- [点金术技术信号汇总]({MAIN_FOLDER}/{MAIN_SUMMARY_MD})",
        f"- [点金术extra]({EXTRA_FOLDER}/总表.md)（{len(extra)} 只，独立文件夹）",
        f"- [点金术extra技术信号汇总]({EXTRA_FOLDER}/{EXTRA_SUMMARY_MD})",
        "",
        "自选股只做标注，不进入筛选股票池。个股目录名带 `_自选` 时，清理仍按代码前缀匹配。",
        "",
    ]
    if not hits:
        index += [EMPTY_MARK, ""]
    (out_dir / "总表.md").write_text("\n".join(index), encoding="utf-8")
    return out_dir
