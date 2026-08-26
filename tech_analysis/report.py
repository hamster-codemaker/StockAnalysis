"""写出个股 signals.csv / 分析报告.md，以及全市场信号汇总。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from .config import cfg_get
from .signals import (
    BOLL_TYPES,
    MA_TYPES,
    MACD_CROSS_TYPES,
    MACD_DIV_TYPES,
    RSI_TYPES,
    Signal,
)

_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')

SUMMARY_TYPES = [
    ("MA8/MA24 金叉死叉", MA_TYPES),
    ("MACD 金叉死叉", MACD_CROSS_TYPES),
    ("MACD 顶/底背离", MACD_DIV_TYPES),
    ("布林上破/下穿", BOLL_TYPES),
    ("RSI 超买/超卖", RSI_TYPES),
]


@dataclass
class StockResult:
    code: str
    name: str
    ok: bool
    error: str = ""
    last_date: str = ""
    last_close: float | None = None
    signals: list[Signal] = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)
    out_dir: Path | None = None
    bars: int = 0
    lookback: int = 0


def sanitize_filename(name: str, max_len: int = 20) -> str:
    text = _ILLEGAL.sub("", str(name)).replace("\u3000", " ").strip().strip(".")
    return text[:max_len].strip() or "unnamed"


def stock_dir_name(code: str, name: str) -> str:
    if name:
        return f"{code}_{sanitize_filename(name)}"
    return code


def _fmt_num(value, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _date_str(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _metrics_text(metrics: dict) -> str:
    parts = []
    for key, val in metrics.items():
        digits = 4 if any(k in key.upper() for k in ("DIF", "DEA", "HIST")) else 3
        if "RSI" in key.upper():
            digits = 2
        parts.append(f"{key}={_fmt_num(val, digits)}")
    return "；".join(parts)


def signals_to_frame(signals: list[Signal]) -> pd.DataFrame:
    rows = [
        {
            "日期": _date_str(s.date),
            "类型": s.signal_type,
            "细节": s.detail,
            "收盘价": round(float(s.close), 4),
            "相关指标": _metrics_text(s.metrics),
        }
        for s in signals
    ]
    return pd.DataFrame(rows, columns=["日期", "类型", "细节", "收盘价", "相关指标"])


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    sep = "|" + " --- |" * len(headers)
    body = ["| " + " | ".join("" if v is None else str(v) for v in row) + " |" for row in rows]
    return "\n".join([head, sep, *body]) if rows else "\n".join([head, sep, "| " + " | ".join("—" for _ in headers) + " |"])


def _latest_in(signals: list[Signal], types: set[str]) -> Signal | None:
    picked = [s for s in signals if s.signal_type in types]
    if not picked:
        return None
    return max(picked, key=lambda s: (pd.Timestamp(s.date), s.signal_type))


def write_daily_csv(df: pd.DataFrame, dest: Path) -> Path:
    cols = [
        "date", "open", "high", "low", "close", "volume",
        "ma_short", "ma_long", "ma_trend", "dif", "dea", "hist",
        "boll_mid", "boll_upper", "boll_lower", "rsi", "rsi2", "rsi3",
    ]
    use = [c for c in cols if c in df.columns]
    out = df[use].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dest, index=False, encoding="utf-8-sig")
    return dest


def write_stock_report(result: StockResult, cfg: dict, dest: Path) -> Path:
    lookback = int(cfg_get(cfg, "lookback_trading_days", 120))
    ma_s = int(cfg_get(cfg, "ma.short", 8))
    ma_l = int(cfg_get(cfg, "ma.long", 24))
    ma_t = int(cfg_get(cfg, "ma.trend", 120))
    div_win = int(cfg_get(cfg, "macd.divergence_window", 60))
    min_gap = int(cfg_get(cfg, "macd.divergence_min_gap", 5))
    rsi_n = int(cfg_get(cfg, "rsi.period", 6))
    rsi_n2 = int(cfg_get(cfg, "rsi.period2", 12))
    rsi_n3 = int(cfg_get(cfg, "rsi.period3", 24))
    rsi_hi = float(cfg_get(cfg, "rsi.overbought", 80))
    rsi_lo = float(cfg_get(cfg, "rsi.oversold", 20))
    boll_n = int(cfg_get(cfg, "bollinger.period", 20))
    boll_k = float(cfg_get(cfg, "bollinger.std_mult", 2.0))
    title = f"{result.code} {result.name}".strip()

    summary_rows = []
    for label, types in SUMMARY_TYPES:
        latest = _latest_in(result.signals, types)
        if latest is None:
            summary_rows.append([label, "否", "—", "回看期内未出现"])
        else:
            summary_rows.append([label, "是", _date_str(latest.date), latest.detail])

    snap = result.snapshot
    snap_rows = [
        ["最新交易日", snap.get("date", result.last_date)],
        ["收盘价", _fmt_num(snap.get("close", result.last_close))],
        [f"MA{ma_s} / MA{ma_l} / MA{ma_t}", f"{_fmt_num(snap.get('ma_short'))} / {_fmt_num(snap.get('ma_long'))} / {_fmt_num(snap.get('ma_trend'))}"],
        ["DIF / DEA / HIST", f"{_fmt_num(snap.get('dif'), 4)} / {_fmt_num(snap.get('dea'), 4)} / {_fmt_num(snap.get('hist'), 4)}"],
        ["布林上 / 中 / 下", f"{_fmt_num(snap.get('boll_upper'))} / {_fmt_num(snap.get('boll_mid'))} / {_fmt_num(snap.get('boll_lower'))}"],
        [f"RSI{rsi_n} / RSI{rsi_n2} / RSI{rsi_n3}", f"{_fmt_num(snap.get('rsi'), 2)} / {_fmt_num(snap.get('rsi2'), 2)} / {_fmt_num(snap.get('rsi3'), 2)}"],
    ]

    detail_df = signals_to_frame(result.signals)
    if detail_df.empty:
        detail_md = "回看期内无信号。"
    else:
        detail_md = _md_table(
            list(detail_df.columns),
            [[str(v) for v in row] for row in detail_df.itertuples(index=False)],
        )

    today_sigs = [s for s in result.signals if _date_str(s.date) == result.last_date]
    if today_sigs:
        today_md = _md_table(
            ["类型", "细节", "收盘价", "相关指标"],
            [
                [s.signal_type, s.detail, _fmt_num(s.close), _metrics_text(s.metrics)]
                for s in today_sigs
            ],
        )
    else:
        today_md = "最新交易日未触发上述信号。"

    lines = [
        f"# {title} 技术面分析",
        "",
        f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M}",
        "- 数据：腾讯前复权日线",
        f"- 分析回看：最近 **{lookback}** 个交易日 K 线（实际可用 {result.lookback} 根；含预热共 {result.bars} 根）",
        f"- 最新交易日：{result.last_date}  收盘 { _fmt_num(result.last_close) }",
        "",
        "## 信号判定规则",
        "",
        f"- **均线金叉**：当日 MA{ma_s} > MA{ma_l}，且前一日 MA{ma_s} ≤ MA{ma_l}。死叉相反。图上另画 MA{ma_t}。",
        "- **MACD 金叉**：当日 DIF > DEA，且前一日 DIF ≤ DEA。死叉相反。HIST = 2×(DIF−DEA)。参数 12/26/9。",
        f"- **MACD 顶背离**：当日收盘达到近 **{div_win}** 日最高（本轮最高的首日），"
        f"在窗口内且至少相隔 {min_gap} 日取前高；价格未低于前高，但 DIF 或 HIST 低于前高当日。",
        f"- **MACD 底背离**：对称（近 {div_win} 日最低的首日，DIF 或 HIST 未创新低）。",
        f"- **布林上破**：前一日收盘 ≤ 前一日上轨，且当日收盘 > 当日上轨"
        f"（周期 {boll_n}，倍数 {boll_k:g}，标准差 ddof=0；通达信/同花顺默认 N=20,P=2）。下穿对称。",
        f"- **RSI 严重超买/超卖**：主周期为通达信 RSI1=RSI({rsi_n})；另画 RSI{rsi_n2}、RSI{rsi_n3}。"
        f"当日 RSI({rsi_n}) ≥ {rsi_hi:g} 或 ≤ {rsi_lo:g}；并标注刚进入/持续，另记录刚离开阈值。",
        "",
        "## 最近信号摘要",
        "",
        _md_table(["类别", "回看期内是否出现", "最近一次日期", "说明"], summary_rows),
        "",
        "## 最新指标快照",
        "",
        _md_table(["项目", "数值"], snap_rows),
        "",
        "## 当日是否触发",
        "",
        today_md,
        "",
        "## 技术分析图",
        "",
        "![技术分析](技术分析.png)",
        "",
        "图中含最近 120 个交易日 K 线、MA8/MA24/MA120、布林、MACD、RSI(6/12/24)。",
        "",
        "## 信号明细",
        "",
        detail_md,
        "",
    ]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


def write_signals_csv(signals: list[Signal], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    signals_to_frame(signals).to_csv(dest, index=False, encoding="utf-8-sig")
    return dest


def _latest_text(signals: list[Signal], types: set[str]) -> str:
    latest = _latest_in(signals, types)
    if latest is None:
        return "无"
    return f"{_date_str(latest.date)} {latest.signal_type}"


def write_summary(results: list[StockResult], dest_md: Path, dest_csv: Path) -> None:
    dest_md.parent.mkdir(parents=True, exist_ok=True)
    rows_md: list[list[str]] = []
    rows_csv: list[dict] = []
    for r in results:
        if not r.ok:
            rows_md.append([r.code, r.name, "失败", r.error, "—", "—", "—", "—", "—", "0"])
            rows_csv.append(
                {
                    "代码": r.code,
                    "名称": r.name,
                    "状态": "失败",
                    "说明": r.error,
                    "最新交易日": "",
                    "收盘价": "",
                    "最近MA交叉": "",
                    "最近MACD交叉": "",
                    "最近背离": "",
                    "最近布林": "",
                    "最近RSI": "",
                    "信号条数": 0,
                }
            )
            continue
        rows_md.append(
            [
                r.code,
                r.name,
                r.last_date,
                _fmt_num(r.last_close),
                _latest_text(r.signals, MA_TYPES),
                _latest_text(r.signals, MACD_CROSS_TYPES),
                _latest_text(r.signals, MACD_DIV_TYPES),
                _latest_text(r.signals, BOLL_TYPES),
                _latest_text(r.signals, RSI_TYPES),
                str(len(r.signals)),
            ]
        )
        rows_csv.append(
            {
                "代码": r.code,
                "名称": r.name,
                "状态": "成功",
                "说明": "",
                "最新交易日": r.last_date,
                "收盘价": r.last_close,
                "最近MA交叉": _latest_text(r.signals, MA_TYPES),
                "最近MACD交叉": _latest_text(r.signals, MACD_CROSS_TYPES),
                "最近背离": _latest_text(r.signals, MACD_DIV_TYPES),
                "最近布林": _latest_text(r.signals, BOLL_TYPES),
                "最近RSI": _latest_text(r.signals, RSI_TYPES),
                "信号条数": len(r.signals),
            }
        )

    headers = ["代码", "名称", "最新交易日", "收盘", "最近MA交叉", "最近MACD交叉", "最近背离", "最近布林", "最近RSI", "信号条数"]
    lines = [
        "# 自选股技术信号汇总",
        "",
        f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M}",
        f"- 共 {len(results)} 只，成功 {sum(1 for r in results if r.ok)} 只",
        "",
        _md_table(headers, rows_md),
        "",
        "说明：上表只列各类**最近一次**信号；完整明细见各股票目录的 `signals.csv` 与 `分析报告.md`。",
        "MACD 背离判定窗口见个股报告（默认近 60 个交易日找前高/前低）。",
        "",
    ]
    dest_md.write_text("\n".join(lines), encoding="utf-8")
    pd.DataFrame(rows_csv).to_csv(dest_csv, index=False, encoding="utf-8-sig")
