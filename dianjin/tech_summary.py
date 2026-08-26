"""把已算好的日线指标压成总表可读的几列。不另拉 K 线。

列来自 enrich 的 snapshot，或缺快照时读个股目录里的 日线指标.csv 最后两行。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dianjin.mdfmt import fmt_num as _fmt
from dianjin.rules import to_float

TECH_COLUMNS = (
    "指标日",
    "MA8",
    "MA24",
    "BOLL",
    "MACD",
    "RSI6",
    "RSI12",
    "RSI24",
)

BLANK = {key: "—" for key in TECH_COLUMNS}


def _row_from_series(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "to_dict"):
        raw = row.to_dict()
    elif isinstance(row, dict):
        raw = dict(row)
    else:
        return {}
    date = raw.get("date") or raw.get("指标日")
    return {
        "date": str(date)[:10] if date not in (None, "") else "",
        "close": to_float(raw.get("close")),
        "ma_short": to_float(raw.get("ma_short")),
        "ma_long": to_float(raw.get("ma_long")),
        "ma_trend": to_float(raw.get("ma_trend")),
        "dif": to_float(raw.get("dif")),
        "dea": to_float(raw.get("dea")),
        "hist": to_float(raw.get("hist")),
        "boll_upper": to_float(raw.get("boll_upper")),
        "boll_mid": to_float(raw.get("boll_mid")),
        "boll_lower": to_float(raw.get("boll_lower")),
        "rsi": to_float(raw.get("rsi")),
        "rsi2": to_float(raw.get("rsi2")),
        "rsi3": to_float(raw.get("rsi3")),
    }


def last_two_indicator_rows(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """读 日线指标.csv 最后一行（及倒数第二行，供 MACD 金死叉）。失败返回 ({}, None)。"""
    dest = Path(path)
    if not dest.is_file():
        return {}, None
    try:
        import pandas as pd

        df = pd.read_csv(dest, encoding="utf-8-sig")
    except Exception:
        return {}, None
    if df.empty:
        return {}, None
    last = _row_from_series(df.iloc[-1])
    prev = _row_from_series(df.iloc[-2]) if len(df) >= 2 else None
    return last, prev


def merge_snapshot(primary: dict[str, Any] | None, fallback: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(fallback or {})
    for key, value in (primary or {}).items():
        if value is not None and value != "":
            out[key] = value
    return out


def boll_position(close: Any, upper: Any, lower: Any) -> str:
    """布林位置：上轨上 / 上沿 / 中轨 / 下沿 / 下轨下，并带 %B。"""
    px = to_float(close)
    hi = to_float(upper)
    lo = to_float(lower)
    if px is None or hi is None or lo is None:
        return "—"
    width = hi - lo
    if width <= 0:
        return "—"
    pctb = (px - lo) / width
    if px > hi:
        label = "上轨上"
    elif px < lo:
        label = "下轨下"
    elif pctb >= 0.66:
        label = "上沿"
    elif pctb <= 0.34:
        label = "下沿"
    else:
        label = "中轨"
    return f"{label} {pctb:.2f}"


def macd_state(
    dif: Any,
    dea: Any,
    hist: Any = None,
    prev_dif: Any = None,
    prev_dea: Any = None,
) -> str:
    """优先标当日金叉/死叉；否则多头/空头。"""
    cur_dif = to_float(dif)
    cur_dea = to_float(dea)
    if cur_dif is None or cur_dea is None:
        return "—"
    old_dif = to_float(prev_dif)
    old_dea = to_float(prev_dea)
    if old_dif is not None and old_dea is not None:
        if old_dif <= old_dea and cur_dif > cur_dea:
            return "金叉"
        if old_dif >= old_dea and cur_dif < cur_dea:
            return "死叉"
    hist_v = to_float(hist)
    if cur_dif > cur_dea:
        return "多头" if hist_v is None or hist_v >= 0 else "多头钝"
    if cur_dif < cur_dea:
        return "空头" if hist_v is None or hist_v <= 0 else "空头钝"
    return "粘合"


def format_tech_cells(
    snap: dict[str, Any] | None,
    prev: dict[str, Any] | None = None,
) -> dict[str, str]:
    """总表用的短字符串。缺数写 —。"""
    if not snap:
        return dict(BLANK)
    date = str(snap.get("date") or snap.get("指标日") or "").strip()[:10]
    return {
        "指标日": date or "—",
        "MA8": _fmt(snap.get("ma_short"), 2),
        "MA24": _fmt(snap.get("ma_long"), 2),
        "BOLL": boll_position(snap.get("close"), snap.get("boll_upper"), snap.get("boll_lower")),
        "MACD": macd_state(
            snap.get("dif"),
            snap.get("dea"),
            snap.get("hist"),
            (prev or {}).get("dif"),
            (prev or {}).get("dea"),
        ),
        "RSI6": _fmt(snap.get("rsi"), 1),
        "RSI12": _fmt(snap.get("rsi2"), 1),
        "RSI24": _fmt(snap.get("rsi3"), 1),
    }


def cells_from_enrich(info: dict | None) -> dict[str, str]:
    if not info:
        return dict(BLANK)
    ready = info.get("tech_cells")
    if isinstance(ready, dict) and all(key in ready for key in TECH_COLUMNS):
        return {key: str(ready.get(key) or "—") for key in TECH_COLUMNS}
    return format_tech_cells(info.get("tech_snap"), info.get("tech_prev"))


def cells_from_daily_csv(path: Path) -> dict[str, str]:
    last, prev = last_two_indicator_rows(path)
    return format_tech_cells(last, prev)


def overview_lines(cells: dict[str, str]) -> list[str]:
    """个股报告里的「技术指标」小节。"""
    return [
        "## 技术指标",
        "",
        f"- 指标日：{cells.get('指标日', '—')}",
        f"- MA8 / MA24：{cells.get('MA8', '—')} / {cells.get('MA24', '—')}",
        f"- BOLL：{cells.get('BOLL', '—')}（%B，上轨上/上沿/中轨/下沿/下轨下）",
        f"- MACD：{cells.get('MACD', '—')}",
        f"- RSI6 / RSI12 / RSI24：{cells.get('RSI6', '—')} / {cells.get('RSI12', '—')} / {cells.get('RSI24', '—')}",
        "",
    ]
