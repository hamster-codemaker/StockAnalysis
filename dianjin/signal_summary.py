"""点金术命中股的技术分析信号汇总。复用 enrich / signals.csv，不另拉 K 线。"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from dianjin.mdfmt import md_table as _md_table
from dianjin.screen import DianjinHit
from dianjin.tech_summary import last_two_indicator_rows
from tech_analysis.report import _date_str, _latest_text
from tech_analysis.signals import (
    BOLL_TYPES,
    MA_TYPES,
    MACD_CROSS_TYPES,
    MACD_DIV_TYPES,
    RSI_TYPES,
    Signal,
)

log = logging.getLogger("dianjin")

EMPTY_MARK = "今日无符合"
NO_SIGNAL_MARK = "今日无信号"

MAIN_SUMMARY_MD = "技术信号汇总.md"
MAIN_SUMMARY_CSV = "技术信号汇总.csv"
EXTRA_SUMMARY_MD = "点金术extra技术信号汇总.md"
EXTRA_SUMMARY_CSV = "点金术extra技术信号汇总.csv"
DAILY_MAIN_SUMMARY_MD = "点金术技术信号汇总.md"
DAILY_MAIN_SUMMARY_CSV = "点金术技术信号汇总.csv"

CSV_COLUMNS = [
    "代码",
    "名称",
    "指标日",
    "今日信号",
    "最近MA交叉",
    "最近MACD交叉",
    "最近背离",
    "最近布林",
    "最近RSI",
    "近5日信号",
]


def _as_signal(item: Any) -> Signal | None:
    if isinstance(item, Signal):
        return item
    if not isinstance(item, dict):
        return None
    raw_date = item.get("date") or item.get("日期")
    kind = str(item.get("signal_type") or item.get("类型") or "").strip()
    if not raw_date or not kind:
        return None
    try:
        date = pd.Timestamp(raw_date)
    except Exception:
        return None
    if pd.isna(date):
        return None
    close = item.get("close") if item.get("close") is not None else item.get("收盘价")
    try:
        close_v = float(close) if close not in (None, "") else 0.0
    except (TypeError, ValueError):
        close_v = 0.0
    return Signal(
        date=date,
        signal_type=kind,
        detail=str(item.get("detail") or item.get("细节") or "").strip(),
        close=close_v,
        metrics=item.get("metrics") if isinstance(item.get("metrics"), dict) else {},
    )


def load_signals_csv(path: Path) -> list[Signal]:
    dest = Path(path)
    if not dest.is_file():
        return []
    try:
        frame = pd.read_csv(dest, encoding="utf-8-sig")
    except Exception:
        return []
    if frame.empty:
        return []
    out: list[Signal] = []
    for rec in frame.to_dict(orient="records"):
        sig = _as_signal(rec)
        if sig is not None:
            out.append(sig)
    return out


def _folder_name(hit: DianjinHit, info: dict | None) -> str:
    from dianjin.watchlist_mark import stock_folder_name

    if info and info.get("folder"):
        return str(info["folder"])
    marked = bool((info or {}).get("in_watchlist"))
    return stock_folder_name(hit.code, hit.name, marked)


def _signals_for_hit(hit: DianjinHit, info: dict | None, bundle_dir: Path) -> list[Signal]:
    if info:
        raw = info.get("signals")
        if raw:
            parsed = [s for s in (_as_signal(item) for item in raw) if s is not None]
            if parsed:
                return parsed
    folder = _folder_name(hit, info)
    return load_signals_csv(Path(bundle_dir) / "个股" / folder / "signals.csv")


def _indicator_date(
    hit: DianjinHit,
    info: dict | None,
    bundle_dir: Path,
    signals: list[Signal],
) -> str:
    if info:
        cells = info.get("tech_cells") if isinstance(info.get("tech_cells"), dict) else {}
        cell_date = str(cells.get("指标日") or "").strip()
        if cell_date and cell_date != "—":
            return cell_date[:10]
        last_date = str(info.get("last_date") or "").strip()
        if last_date:
            return last_date[:10]
        snap = info.get("tech_snap") if isinstance(info.get("tech_snap"), dict) else {}
        snap_date = str(snap.get("date") or "").strip()
        if snap_date:
            return snap_date[:10]
    folder = _folder_name(hit, info)
    last, _prev = last_two_indicator_rows(Path(bundle_dir) / "个股" / folder / "日线指标.csv")
    csv_date = str(last.get("date") or "").strip()
    if csv_date:
        return csv_date[:10]
    if signals:
        return max(_date_str(s.date) for s in signals)
    return ""


def _today_signals(signals: list[Signal], indicator_date: str) -> list[Signal]:
    if not indicator_date:
        return []
    return [s for s in signals if _date_str(s.date) == indicator_date]


def _today_text(today: list[Signal]) -> str:
    if not today:
        return NO_SIGNAL_MARK
    return "；".join(s.signal_type for s in today)


def _recent_text(info: dict | None, today: list[Signal]) -> str:
    if info and info.get("recent_signals"):
        return str(info["recent_signals"])
    if today:
        return "；".join(f"{_date_str(s.date)} {s.signal_type}" for s in today)
    return "无"


def _row_dict(
    hit: DianjinHit,
    info: dict | None,
    bundle_dir: Path,
) -> dict[str, Any]:
    signals = _signals_for_hit(hit, info, bundle_dir)
    day = _indicator_date(hit, info, bundle_dir, signals)
    today = _today_signals(signals, day)
    return {
        "代码": hit.code,
        "名称": hit.name,
        "指标日": day or "—",
        "今日信号": _today_text(today),
        "最近MA交叉": _latest_text(signals, MA_TYPES),
        "最近MACD交叉": _latest_text(signals, MACD_CROSS_TYPES),
        "最近背离": _latest_text(signals, MACD_DIV_TYPES),
        "最近布林": _latest_text(signals, BOLL_TYPES),
        "最近RSI": _latest_text(signals, RSI_TYPES),
        "近5日信号": _recent_text(info, today),
        "_today": today,
    }


def _section_lines(row: dict[str, Any]) -> list[str]:
    lines = [
        f"## {row['代码']} {row['名称']}",
        "",
        f"- 指标日：{row['指标日']}",
        f"- 今日信号：{row['今日信号']}",
    ]
    today = row.get("_today") or []
    if today:
        lines.append("")
        for sig in today:
            detail = f"：{sig.detail}" if sig.detail else ""
            lines.append(f"- {_date_str(sig.date)} {sig.signal_type}{detail}")
    lines += [""]
    return lines


def render_signal_summary_md(
    hits: list[DianjinHit],
    *,
    title: str,
    stamp: str,
    rows: list[dict[str, Any]],
) -> str:
    lines = [
        f"# {title}技术信号汇总 {stamp}",
        "",
        f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M}",
        f"- 入选：{len(hits)} 只",
        "- 本文件只汇总点金术命中股的技术分析信号（金叉死叉、布林、RSI、均线等已检出项），"
        "不含自选股池；原始均线/RSI 数值见总表。",
        "",
    ]
    if not hits:
        lines += [EMPTY_MARK, ""]
        return "\n".join(lines)

    table_rows = [[row[col] for col in CSV_COLUMNS] for row in rows]
    lines += [
        _md_table(CSV_COLUMNS, table_rows),
        "",
        "上表「今日信号」为指标日当天已检出的类型；无则记「今日无信号」。"
        "「最近*」为回看期内各类最近一次，完整明细见各股 `signals.csv`。",
        "",
    ]
    for row in rows:
        lines += _section_lines(row)
    return "\n".join(lines)


def write_signal_summary(
    hits: list[DianjinHit],
    bundle_dir: Path,
    *,
    title: str,
    stamp: str,
    md_name: str,
    csv_name: str,
    info_map: dict[str, dict] | None = None,
) -> tuple[Path, Path]:
    """写出文件夹根下的技术信号汇总（md + csv）。空名单写「今日无符合」，不抛错。"""
    dest = Path(bundle_dir)
    dest.mkdir(parents=True, exist_ok=True)
    mapping = info_map or {}
    rows: list[dict[str, Any]] = []
    csv_rows: list[dict[str, str]] = []
    for hit in hits:
        info = mapping.get(str(hit.code)) or mapping.get(str(hit.code).zfill(6))
        row = _row_dict(hit, info, dest)
        rows.append(row)
        csv_rows.append({col: row[col] for col in CSV_COLUMNS})

    md_path = dest / md_name
    csv_path = dest / csv_name
    md_path.write_text(
        render_signal_summary_md(hits, title=title, stamp=stamp, rows=rows),
        encoding="utf-8",
    )
    pd.DataFrame(csv_rows, columns=CSV_COLUMNS).to_csv(csv_path, index=False, encoding="utf-8-sig")
    log.info("%s技术信号汇总已写入：%s（%d 只）", title, md_path, len(hits))
    return md_path, csv_path


def write_bundle_signal_summaries(
    hits: list[DianjinHit],
    extra: list[DianjinHit],
    main_path: Path,
    extra_path: Path,
    *,
    stamp: str,
    info_map: dict[str, dict] | None = None,
    extra_map: dict[str, dict] | None = None,
) -> None:
    write_signal_summary(
        hits,
        main_path,
        title="点金术",
        stamp=stamp,
        md_name=MAIN_SUMMARY_MD,
        csv_name=MAIN_SUMMARY_CSV,
        info_map=info_map,
    )
    write_signal_summary(
        extra,
        extra_path,
        title="点金术extra",
        stamp=stamp,
        md_name=EXTRA_SUMMARY_MD,
        csv_name=EXTRA_SUMMARY_CSV,
        info_map=extra_map or info_map,
    )


def _copy_or_empty(src: Path, dest: Path) -> None:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if Path(src).is_file():
        shutil.copy2(src, dest)
        return
    if dest.suffix.lower() == ".md":
        dest.write_text(f"{EMPTY_MARK}\n", encoding="utf-8")
    else:
        pd.DataFrame(columns=CSV_COLUMNS).to_csv(dest, index=False, encoding="utf-8-sig")


def copy_summaries_to_daily_root(
    report_root: Path,
    dj_dest: Path,
    dj_extra_dest: Path,
) -> None:
    """把点金术 / extra 信号汇总再放一份到日报日期根目录，文件名互不覆盖。"""
    root = Path(report_root)
    main = Path(dj_dest)
    extra = Path(dj_extra_dest)
    _copy_or_empty(main / MAIN_SUMMARY_MD, root / DAILY_MAIN_SUMMARY_MD)
    _copy_or_empty(main / MAIN_SUMMARY_CSV, root / DAILY_MAIN_SUMMARY_CSV)
    _copy_or_empty(extra / EXTRA_SUMMARY_MD, root / EXTRA_SUMMARY_MD)
    _copy_or_empty(extra / EXTRA_SUMMARY_CSV, root / EXTRA_SUMMARY_CSV)
