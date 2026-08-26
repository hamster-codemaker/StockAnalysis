"""从东方财富公开财报提取关键财务数据，计算同比/环比并输出图表。

不解析巨潮扫描版 PDF。数据口径与披露定期报告一致（东方财富 F10 /
财务分析主要指标 + 利润表/资产负债表/现金流量表）。
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

FINANCE_DIR_NAME = "财务分析"

# 图表与报告中必须尽量覆盖的指标；取不到则跳过并在 Markdown 中注明
REQUIRED_METRICS = [
    "营业收入",
    "归母净利润",
    "扣非净利润",
    "毛利率",
    "净利率",
    "ROE",
    "经营现金流净额",
    "资产负债率",
    "流动比率",
    "基本每股收益",
]

_AMOUNT_METRICS = ("营业收入", "归母净利润", "扣非净利润", "经营现金流净额")
_RATE_METRICS = ("毛利率", "净利率", "ROE", "资产负债率")
_RATIO_METRICS = ("流动比率",)
_EPS_METRICS = ("基本每股收益",)

_PERIOD_SUFFIX = {
    "03-31": "一季报",
    "06-30": "半年报",
    "09-30": "三季报",
    "12-31": "年报",
}


def _find_col(df: pd.DataFrame | None, *keywords: str) -> str | None:
    """优先精确匹配任一关键词，否则返回包含该关键词的首个列名（OR）。"""
    if df is None or df.empty or not keywords:
        return None
    cols = [str(c) for c in df.columns]
    for kw in keywords:
        for col in cols:
            if col == kw:
                return col
    for kw in keywords:
        for col in cols:
            if kw in col:
                return col
    return None


def _retry(func, label: str, tries: int = 3, wait: float = 4.0):
    for attempt in range(1, tries + 1):
        try:
            return func()
        except Exception as exc:
            log.warning("%s 获取失败（第%d次）：%s", label, attempt, exc)
            if attempt < tries:
                time.sleep(wait)
    return None


def em_f10_symbol(code: str) -> str:
    """利润表/资产负债表/现金流量表使用的市场前缀代码，如 SH688308。"""
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return f"SH{code}"
    if code.startswith(("4", "8")):
        return f"BJ{code}"
    return f"SZ{code}"


def em_indicator_symbol(code: str) -> str:
    """财务分析主要指标使用的代码，如 688308.SH。"""
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _norm_date(value) -> str:
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return ""
    return text[:10]


def _period_type(date_str: str, fallback: str = "") -> str:
    suffix = _PERIOD_SUFFIX.get(date_str[5:10] if len(date_str) >= 10 else "", "")
    return suffix or (str(fallback).strip() if fallback and str(fallback) != "nan" else "")


def _short_label(date_str: str) -> str:
    if len(date_str) < 10:
        return date_str
    year, md = date_str[:4], date_str[5:10]
    mapping = {"03-31": "一季", "06-30": "半年", "09-30": "三季", "12-31": "年报"}
    return f"{year}{mapping.get(md, md)}"


def _by_date(df: pd.DataFrame | None, date_col: str | None, value_col: str | None) -> dict[str, float]:
    if df is None or df.empty or not date_col or not value_col:
        return {}
    dates = df[date_col].map(_norm_date)
    values = pd.to_numeric(df[value_col], errors="coerce")
    out: dict[str, float] = {}
    for date_str, value in zip(dates, values):
        if date_str:
            out[date_str] = float(value) if pd.notna(value) else float("nan")
    return out


def _first_map(*maps: dict[str, float]) -> dict[str, float]:
    """按优先级合并：后者仅填补前者缺失/NaN 的日期。"""
    merged: dict[str, float] = {}
    for mapping in maps:
        for key, value in mapping.items():
            old = merged.get(key)
            if old is None or pd.isna(old):
                merged[key] = value
    return merged


def _safe_div(numer: float, denom: float, scale: float = 1.0) -> float:
    if pd.isna(numer) or pd.isna(denom) or denom == 0:
        return float("nan")
    return numer / denom * scale


def _pct_change(current: float, previous: float) -> float:
    """同比/环比：分母用上年同期（或上期）绝对值；分母为 0 则不适用。"""
    if pd.isna(current) or pd.isna(previous) or previous == 0:
        return float("nan")
    return (current - previous) / abs(previous) * 100.0


def _yoy_is_low_base(current, previous, pct, *, extreme: float = 400.0) -> bool:
    """上年同期过小或由亏转盈导致百分比爆炸时，图表上视为低基数。"""
    if pct is None or pd.isna(pct):
        return False
    if previous is None or pd.isna(previous) or previous == 0:
        return abs(float(pct)) >= extreme
    if abs(float(pct)) < extreme:
        return False
    if current is None or pd.isna(current):
        return True
    return abs(float(previous)) < abs(float(current)) * 0.25


def _yoy_view_limit(*series: pd.Series | None) -> float:
    """同比图可视窗口：中位数 + 4×IQR，夹在 ±150%～±300%，避免单点撑轴。"""
    parts = [pd.to_numeric(s, errors="coerce") for s in series if s is not None]
    if not parts:
        return 200.0
    vals = pd.concat(parts, ignore_index=True).dropna()
    if vals.empty:
        return 200.0
    abs_v = vals.abs()
    q1 = float(abs_v.quantile(0.25))
    q3 = float(abs_v.quantile(0.75))
    iqr = max(q3 - q1, 15.0)
    raw = float(abs_v.median()) + 4.0 * iqr
    return float(min(300.0, max(150.0, raw)))


def _prior_same_period(detail: pd.DataFrame, idx: int, amount_col: str):
    date_str = _norm_date(detail.iloc[idx].get("报告期"))
    if len(date_str) < 10:
        return float("nan")
    try:
        prev_key = f"{int(date_str[:4]) - 1}{date_str[4:]}"
    except (TypeError, ValueError):
        return float("nan")
    dates = detail["报告期"].map(_norm_date)
    hit = detail.loc[dates == prev_key]
    if hit.empty or amount_col not in hit.columns:
        return float("nan")
    return pd.to_numeric(hit.iloc[0][amount_col], errors="coerce")


def _pp_change(current: float, previous: float) -> float:
    if pd.isna(current) or pd.isna(previous):
        return float("nan")
    return current - previous


def _yoy_qoq(dates: list[str], values: list[float], *, points: bool = False) -> tuple[list[float], list[float]]:
    """同比=上年同期；环比=相邻报告期（累计口径，年报↔一季报不可比）。"""
    lookup = dict(zip(dates, values))
    ordered = sorted(set(d for d in dates if d))
    prev_period = {ordered[i]: ordered[i - 1] for i in range(1, len(ordered))}
    change = _pp_change if points else _pct_change
    yoy, qoq = [], []
    for date_str, value in zip(dates, values):
        try:
            year = int(date_str[:4])
            prev_year_key = f"{year - 1}{date_str[4:]}"
        except (TypeError, ValueError):
            prev_year_key = ""
        yoy.append(change(value, lookup.get(prev_year_key, float("nan"))))
        qoq.append(change(value, lookup.get(prev_period.get(date_str, ""), float("nan"))))
    return yoy, qoq


_QUARTER_END = ("03-31", "06-30", "09-30", "12-31")
_QUARTER_PREV_MD = {"06-30": "03-31", "09-30": "06-30", "12-31": "09-30"}
_QUARTER_METRICS = ("营业收入", "归母净利润")


def _quarter_label(date_str: str) -> str:
    if len(date_str) < 10:
        return date_str
    mapping = {"03-31": "Q1", "06-30": "Q2", "09-30": "Q3", "12-31": "Q4"}
    return f"{date_str[:4]}{mapping.get(date_str[5:10], date_str[5:10])}"


def _quarter_calendar(start_date: str, end_date: str) -> list[str]:
    """从首个可拆单季到最新一期，按 Q1–Q4 补齐日历（缺期留空，便于看前后季）。"""
    start, end = _norm_date(start_date), _norm_date(end_date)
    if len(start) < 10 or len(end) < 10:
        return []
    y0, y1 = int(start[:4]), int(end[:4])
    out: list[str] = []
    for year in range(y0, y1 + 1):
        for md in _QUARTER_END:
            date_str = f"{year}-{md}"
            if start <= date_str <= end:
                out.append(date_str)
    return out


def _quarterly_from_cumulative(dates: list[str], values: list[float]) -> list[float]:
    """累计拆单季：Q1=一季报，Q2=半年报−一季报，Q3=三季报−半年报，Q4=年报−三季报。"""
    lookup = {d: v for d, v in zip(dates, values) if d}
    out: list[float] = []
    for date_str, value in zip(dates, values):
        if not date_str or len(date_str) < 10 or pd.isna(value):
            out.append(float("nan"))
            continue
        md = date_str[5:10]
        year = date_str[:4]
        if md == "03-31":
            out.append(float(value))
            continue
        prev_md = _QUARTER_PREV_MD.get(md)
        prev = lookup.get(f"{year}-{prev_md}", float("nan")) if prev_md else float("nan")
        out.append(float(value) - float(prev) if pd.notna(prev) else float("nan"))
    return out


def _add_quarterly_columns(detail: pd.DataFrame) -> pd.DataFrame:
    """按累计数拆单季，并计算单季同比（历年同季）与单季环比（上一自然季）。"""
    if detail is None or detail.empty or "报告期" not in detail.columns:
        return detail
    df = detail.copy()
    df["报告期"] = df["报告期"].map(_norm_date)
    df = df[df["报告期"] != ""].sort_values("报告期", ascending=True).reset_index(drop=True)
    dates = df["报告期"].tolist()
    for metric in _QUARTER_METRICS:
        raw_col = f"_{metric}_raw"
        wan_col = f"{metric}(万元)"
        if raw_col in df.columns:
            raw = pd.to_numeric(df[raw_col], errors="coerce").tolist()
        elif wan_col in df.columns:
            raw = (pd.to_numeric(df[wan_col], errors="coerce") * 1e4).tolist()
        else:
            continue
        quarterly = _quarterly_from_cumulative(dates, raw)
        yoy, qoq = _yoy_qoq(dates, quarterly, points=False)
        df[f"_{metric}_单季_raw"] = quarterly
        df[f"单季{metric}(万元)"] = [v / 1e4 if pd.notna(v) else float("nan") for v in quarterly]
        df[f"单季{metric}同比(%)"] = yoy
        df[f"单季{metric}环比(%)"] = qoq
    return df


def _align_to_dates(detail: pd.DataFrame, col: str, calendar: list[str]) -> pd.Series:
    if col not in detail.columns:
        return pd.Series([float("nan")] * len(calendar))
    series = pd.Series(
        pd.to_numeric(detail[col], errors="coerce").to_numpy(),
        index=detail["报告期"].map(_norm_date),
    )
    series = series[~series.index.duplicated(keep="last")]
    return pd.Series([float(series[d]) if d in series.index else float("nan") for d in calendar])


def _draw_clipped_pct_lines(ax, x: list[int], items: list[tuple], ylim: float) -> bool:
    """同比/环比折线：超出可视窗口的点断开并标三角。items=(series, label, color, current, previous)。"""
    any_extreme = False
    for series, label, color, currents, previouss in items:
        if series is None:
            continue
        s = pd.to_numeric(pd.Series(list(series)), errors="coerce")
        if not s.notna().any():
            continue
        ax.plot(x, s.where(s.abs() <= ylim), marker="o", linewidth=1.8, label=label, color=color)
        for i, val in enumerate(s):
            if pd.isna(val) or abs(float(val)) <= ylim:
                continue
            any_extreme = True
            current = currents[i] if currents is not None and i < len(currents) else float("nan")
            previous = previouss[i] if previouss is not None and i < len(previouss) else float("nan")
            low = _yoy_is_low_base(current, previous, val)
            mark_y = ylim * (0.90 if float(val) > 0 else -0.90)
            ax.scatter([x[i]], [mark_y], marker="^" if float(val) > 0 else "v", s=55, color=color, zorder=6)
            ax.annotate(
                f"{'低基数' if low else '极端'}\n{float(val):+.0f}%",
                (x[i], mark_y),
                textcoords="offset points",
                xytext=(0, 12 if float(val) > 0 else -18),
                ha="center",
                fontsize=7,
                color=color,
            )
    return any_extreme


def _expand_pct_ylim(ylim: float, series: pd.Series | None, currents, previouss) -> float:
    if series is None:
        return ylim
    s = pd.to_numeric(pd.Series(list(series)), errors="coerce")
    for i, val in enumerate(s):
        if pd.isna(val) or abs(float(val)) > 350:
            continue
        current = currents[i] if currents is not None and i < len(currents) else float("nan")
        previous = previouss[i] if previouss is not None and i < len(previouss) else float("nan")
        if _yoy_is_low_base(current, previous, val):
            continue
        ylim = max(ylim, min(300.0, abs(float(val)) * 1.08))
    return ylim


def _fetch_frames(code: str) -> dict[str, pd.DataFrame | None]:
    import akshare as ak

    symbol_f10 = em_f10_symbol(code)
    symbol_ind = em_indicator_symbol(code)
    frames = {
        "indicator": _retry(
            lambda: ak.stock_financial_analysis_indicator_em(symbol_ind, "按报告期"),
            f"财务指标 {code}",
        ),
        "profit": _retry(
            lambda: ak.stock_profit_sheet_by_report_em(symbol_f10),
            f"利润表 {code}",
        ),
        "balance": _retry(
            lambda: ak.stock_balance_sheet_by_report_em(symbol_f10),
            f"资产负债表 {code}",
        ),
        "cash": _retry(
            lambda: ak.stock_cash_flow_sheet_by_report_em(symbol_f10),
            f"现金流量表 {code}",
        ),
    }
    for name, df in list(frames.items()):
        if df is None or (isinstance(df, pd.DataFrame) and df.empty):
            frames[name] = None
            log.warning("  %s 无数据，将用其余接口补齐", name)
    return frames


def _extract_metrics(frames: dict[str, pd.DataFrame | None]) -> tuple[pd.DataFrame, list[str]]:
    """从多张表抽取统一指标，返回（按报告期升序的明细, 缺失指标名）。"""
    ind, profit, balance, cash = (
        frames.get("indicator"),
        frames.get("profit"),
        frames.get("balance"),
        frames.get("cash"),
    )
    date_ind = _find_col(ind, "REPORT_DATE", "报告期")
    date_p = _find_col(profit, "REPORT_DATE", "报告期")
    date_b = _find_col(balance, "REPORT_DATE", "报告期")
    date_c = _find_col(cash, "REPORT_DATE", "报告期")

    revenue = _first_map(
        _by_date(profit, date_p, _find_col(profit, "TOTAL_OPERATE_INCOME", "OPERATE_INCOME", "营业总收入")),
        _by_date(ind, date_ind, _find_col(ind, "TOTALOPERATEREVE", "营业收入")),
    )
    parent_np = _first_map(
        _by_date(profit, date_p, _find_col(profit, "PARENT_NETPROFIT", "归属于母公司")),
        _by_date(ind, date_ind, _find_col(ind, "PARENTNETPROFIT", "归母净利润")),
        _by_date(profit, date_p, _find_col(profit, "NETPROFIT", "净利润")),
    )
    deduct_np = _first_map(
        _by_date(profit, date_p, _find_col(profit, "DEDUCT_PARENT_NETPROFIT", "扣非")),
        _by_date(ind, date_ind, _find_col(ind, "KCFJCXSYJLR", "扣非")),
    )
    eps = _first_map(
        _by_date(profit, date_p, _find_col(profit, "BASIC_EPS", "基本每股收益")),
        _by_date(ind, date_ind, _find_col(ind, "EPSJB", "基本每股收益")),
    )
    ocf = _by_date(cash, date_c, _find_col(cash, "NETCASH_OPERATE", "NETCASH_OPERATENOTE"))
    if not ocf:
        ocf_ratio = _by_date(ind, date_ind, _find_col(ind, "JYXJLYYSR"))
        ocf = {d: ocf_ratio[d] * revenue[d] if d in revenue else float("nan") for d in ocf_ratio}

    gross = _by_date(ind, date_ind, _find_col(ind, "XSMLL", "毛利率"))
    if not any(pd.notna(v) for v in gross.values()):
        cost = _by_date(profit, date_p, _find_col(profit, "OPERATE_COST", "营业成本"))
        gross = {d: _safe_div(revenue.get(d, float("nan")) - cost.get(d, float("nan")), revenue.get(d, float("nan")), 100.0) for d in revenue}
    net_margin = _by_date(ind, date_ind, _find_col(ind, "XSJLL", "净利率"))
    if not any(pd.notna(v) for v in net_margin.values()):
        net_margin = {d: _safe_div(parent_np.get(d, float("nan")), revenue.get(d, float("nan")), 100.0) for d in revenue}
    roe = _by_date(ind, date_ind, _find_col(ind, "ROEJQ", "ROE", "净资产收益率"))
    debt = _by_date(ind, date_ind, _find_col(ind, "ZCFZL", "资产负债率"))
    if not any(pd.notna(v) for v in debt.values()):
        assets = _by_date(balance, date_b, _find_col(balance, "TOTAL_ASSETS", "总资产"))
        liab = _by_date(balance, date_b, _find_col(balance, "TOTAL_LIABILITIES", "总负债"))
        debt = {d: _safe_div(liab.get(d, float("nan")), assets.get(d, float("nan")), 100.0) for d in assets}
    current = _by_date(ind, date_ind, _find_col(ind, "LD", "流动比率"))
    if not any(pd.notna(v) for v in current.values()):
        ca = _by_date(balance, date_b, _find_col(balance, "TOTAL_CURRENT_ASSETS", "流动资产"))
        cl = _by_date(balance, date_b, _find_col(balance, "TOTAL_CURRENT_LIAB", "流动负债"))
        current = {d: _safe_div(ca.get(d, float("nan")), cl.get(d, float("nan"))) for d in ca}

    type_map: dict[str, str] = {}
    for df, dcol, tcol in (
        (ind, date_ind, _find_col(ind, "REPORT_TYPE", "报告类型")),
        (profit, date_p, _find_col(profit, "REPORT_TYPE", "报告类型")),
    ):
        if df is None or not dcol:
            continue
        types = df[tcol] if tcol else [""] * len(df)
        for raw_date, raw_type in zip(df[dcol], types):
            date_str = _norm_date(raw_date)
            if date_str and date_str not in type_map:
                type_map[date_str] = _period_type(date_str, raw_type)

    dates = sorted({
        d for mapping in (revenue, parent_np, deduct_np, eps, ocf, gross, net_margin, roe, debt, current)
        for d in mapping
        if d
    })
    if not dates:
        return pd.DataFrame(), list(REQUIRED_METRICS)

    raw = {
        "营业收入": [revenue.get(d, float("nan")) for d in dates],
        "归母净利润": [parent_np.get(d, float("nan")) for d in dates],
        "扣非净利润": [deduct_np.get(d, float("nan")) for d in dates],
        "经营现金流净额": [ocf.get(d, float("nan")) for d in dates],
        "毛利率": [gross.get(d, float("nan")) for d in dates],
        "净利率": [net_margin.get(d, float("nan")) for d in dates],
        "ROE": [roe.get(d, float("nan")) for d in dates],
        "资产负债率": [debt.get(d, float("nan")) for d in dates],
        "流动比率": [current.get(d, float("nan")) for d in dates],
        "基本每股收益": [eps.get(d, float("nan")) for d in dates],
    }
    rows: dict[str, list] = {
        "报告期": dates,
        "报告类型": [type_map.get(d) or _period_type(d) for d in dates],
    }
    for name, values in raw.items():
        points = name in _RATE_METRICS
        yoy, qoq = _yoy_qoq(dates, values, points=points)
        if name in _AMOUNT_METRICS:
            rows[f"{name}(万元)"] = [v / 1e4 if pd.notna(v) else float("nan") for v in values]
            rows[f"{name}同比(%)"] = yoy
            rows[f"{name}环比(%)"] = qoq
        elif name in _RATE_METRICS:
            rows[f"{name}(%)"] = values
            rows[f"{name}同比(百分点)"] = yoy
            rows[f"{name}环比(百分点)"] = qoq
        elif name in _RATIO_METRICS:
            rows[name] = values
            rows[f"{name}同比(%)"] = _yoy_qoq(dates, values, points=False)[0]
            rows[f"{name}环比(%)"] = _yoy_qoq(dates, values, points=False)[1]
        else:
            rows[f"{name}(元)"] = values
            rows[f"{name}同比(%)"] = yoy
            rows[f"{name}环比(%)"] = qoq
        # 保留原始金额（元）供作图
        rows[f"_{name}_raw"] = values

    detail = pd.DataFrame(rows)
    missing = [
        name for name in REQUIRED_METRICS
        if name not in raw or not any(pd.notna(v) for v in raw[name])
    ]
    return detail, missing


def _setup_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for font_path in (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\msyh.ttf"),
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
    ):
        if font_path.exists():
            try:
                font_manager.fontManager.addfont(str(font_path))
            except Exception:
                pass
            break
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _style_axis(ax, title: str, ylabel: str, labels: list[str]) -> None:
    ax.set_title(title, fontsize=13, pad=10)
    ax.set_ylabel(ylabel)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=55, ha="right", fontsize=8)
    ax.grid(True, linestyle="--", alpha=0.35)
    ax.legend(loc="best", fontsize=9)


def _save_fig(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=140, bbox_inches="tight")
    fig.clf()
    return path


def _plot_charts(detail: pd.DataFrame, code: str, name: str, out_dir: Path) -> list[tuple[str, Path]]:
    plt = _setup_matplotlib()
    labels = [_short_label(d) for d in detail["报告期"]]
    x = list(range(len(labels)))
    title_prefix = f"{name}（{code}）"
    charts: list[tuple[str, Path]] = []

    def raw(metric: str) -> pd.Series:
        col = f"_{metric}_raw"
        if col not in detail.columns:
            return pd.Series([float("nan")] * len(detail))
        return pd.to_numeric(detail[col], errors="coerce")

    # 1. 营收与净利润（双轴，亿元）
    rev = raw("营业收入") / 1e8
    np_ = raw("归母净利润") / 1e8
    if rev.notna().any() or np_.notna().any():
        fig, ax1 = plt.subplots(figsize=(12, 5.6))
        if rev.notna().any():
            ax1.bar(x, rev, color="#4C78A8", alpha=0.75, label="营业收入")
        ax1.set_ylabel("营业收入（亿元）")
        ax2 = ax1.twinx()
        if np_.notna().any():
            ax2.plot(x, np_, color="#F58518", marker="o", linewidth=2, label="归母净利润")
        ax2.set_ylabel("归母净利润（亿元）")
        ax1.set_title(f"{title_prefix} 营收与净利润趋势", fontsize=13, pad=10)
        ax1.set_xticks(x)
        ax1.set_xticklabels(labels, rotation=55, ha="right", fontsize=8)
        ax1.grid(True, linestyle="--", alpha=0.35)
        h1, l1 = ax1.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax1.legend(h1 + h2, l1 + l2, loc="best", fontsize=9)
        charts.append(("营收与净利润趋势", _save_fig(fig, out_dir / "营收与净利润趋势.png")))
        plt.close(fig)

    # 2. 利润率 / ROE
    series_rate = [
        ("毛利率", raw("毛利率"), "#54A24B"),
        ("净利率", raw("净利率"), "#E45756"),
        ("ROE", raw("ROE"), "#B279A2"),
    ]
    if any(s.notna().any() for _, s, _ in series_rate):
        fig, ax = plt.subplots(figsize=(12, 5.6))
        for label, series, color in series_rate:
            if series.notna().any():
                ax.plot(x, series, marker="o", linewidth=1.8, label=label, color=color)
        _style_axis(ax, f"{title_prefix} 利润率与 ROE 趋势", "比率（%）", labels)
        charts.append(("利润率与ROE趋势", _save_fig(fig, out_dir / "利润率与ROE趋势.png")))
        plt.close(fig)

    # 3. 经营现金流 vs 净利润
    ocf = raw("经营现金流净额") / 1e8
    if ocf.notna().any() or np_.notna().any():
        fig, ax = plt.subplots(figsize=(12, 5.6))
        if ocf.notna().any():
            ax.plot(x, ocf, marker="s", linewidth=1.8, label="经营现金流净额", color="#4C78A8")
        if np_.notna().any():
            ax.plot(x, np_, marker="o", linewidth=1.8, label="归母净利润", color="#F58518")
        ax.axhline(0, color="#666666", linewidth=0.8)
        _style_axis(ax, f"{title_prefix} 经营现金流 vs 净利润", "金额（亿元）", labels)
        charts.append(("经营现金流vs净利润", _save_fig(fig, out_dir / "经营现金流vs净利润.png")))
        plt.close(fig)

    # 4. 同比增速（上年同期；极端/低基数不连线，避免单点撑爆坐标轴）
    yoy_rev = pd.to_numeric(detail.get("营业收入同比(%)"), errors="coerce")
    yoy_np = pd.to_numeric(detail.get("归母净利润同比(%)"), errors="coerce")
    if (yoy_rev is not None and yoy_rev.notna().any()) or (yoy_np is not None and yoy_np.notna().any()):
        fig, ax = plt.subplots(figsize=(12, 5.6))
        rev_now = pd.to_numeric(detail.get("营业收入(万元)"), errors="coerce")
        np_now = pd.to_numeric(detail.get("归母净利润(万元)"), errors="coerce")
        rev_prev = [ _prior_same_period(detail, i, "营业收入(万元)") for i in range(len(detail)) ]
        np_prev = [ _prior_same_period(detail, i, "归母净利润(万元)") for i in range(len(detail)) ]
        ylim = _yoy_view_limit(yoy_rev, yoy_np)
        ylim = _expand_pct_ylim(ylim, yoy_rev, rev_now.tolist() if rev_now is not None else None, rev_prev)
        ylim = _expand_pct_ylim(ylim, yoy_np, np_now.tolist() if np_now is not None else None, np_prev)
        clipped = _draw_clipped_pct_lines(
            ax,
            x,
            [
                (yoy_rev, "营业收入同比", "#4C78A8", rev_now.tolist() if rev_now is not None else None, rev_prev),
                (yoy_np, "归母净利润同比", "#F58518", np_now.tolist() if np_now is not None else None, np_prev),
            ],
            ylim,
        )
        ax.axhline(0, color="#666666", linewidth=0.8)
        ax.set_ylim(-ylim * 1.18, ylim * 1.22)
        _style_axis(ax, f"{title_prefix} 同比增速（上年同期）", "同比（%）", labels)
        if clipped:
            ax.text(
                0.01,
                0.02,
                "已截断极端值（三角）：上年同期绝对值过小或由亏转盈时百分比不宜线性连线。CSV 仍保留原值。",
                transform=ax.transAxes,
                fontsize=8,
                color="#444444",
            )
        charts.append(("营收净利同比增速", _save_fig(fig, out_dir / "营收净利同比增速.png")))
        plt.close(fig)

    # 5. 单季对比（累计拆分，画出全部能拆出的历史单季，不只最近几季）
    q_rev_wan = (
        pd.to_numeric(detail["单季营业收入(万元)"], errors="coerce")
        if "单季营业收入(万元)" in detail.columns
        else pd.Series(dtype=float)
    )
    q_np_wan = (
        pd.to_numeric(detail["单季归母净利润(万元)"], errors="coerce")
        if "单季归母净利润(万元)" in detail.columns
        else pd.Series(dtype=float)
    )
    if q_rev_wan.notna().any() or q_np_wan.notna().any():
        mask = pd.Series(False, index=detail.index)
        if "单季营业收入(万元)" in detail.columns:
            mask = mask | pd.to_numeric(detail["单季营业收入(万元)"], errors="coerce").notna()
        if "单季归母净利润(万元)" in detail.columns:
            mask = mask | pd.to_numeric(detail["单季归母净利润(万元)"], errors="coerce").notna()
        usable = detail.loc[mask, "报告期"].map(_norm_date)
        usable = [d for d in usable if d]
        calendar = _quarter_calendar(min(usable), max(usable)) if usable else []
        if calendar:
            q_labels = [_quarter_label(d) for d in calendar]
            qx = list(range(len(q_labels)))
            q_rev = _align_to_dates(detail, "单季营业收入(万元)", calendar) / 1e4
            q_np = _align_to_dates(detail, "单季归母净利润(万元)", calendar) / 1e4
            q_rev_yoy = _align_to_dates(detail, "单季营业收入同比(%)", calendar)
            q_np_yoy = _align_to_dates(detail, "单季归母净利润同比(%)", calendar)
            q_rev_qoq = _align_to_dates(detail, "单季营业收入环比(%)", calendar)
            q_np_qoq = _align_to_dates(detail, "单季归母净利润环比(%)", calendar)
            q_rev_amt = _align_to_dates(detail, "单季营业收入(万元)", calendar)
            q_np_amt = _align_to_dates(detail, "单季归母净利润(万元)", calendar)
            rev_yoy_prev = [q_rev_amt.iloc[i - 4] if i >= 4 else float("nan") for i in range(len(calendar))]
            np_yoy_prev = [q_np_amt.iloc[i - 4] if i >= 4 else float("nan") for i in range(len(calendar))]
            rev_qoq_prev = [q_rev_amt.iloc[i - 1] if i >= 1 else float("nan") for i in range(len(calendar))]
            np_qoq_prev = [q_np_amt.iloc[i - 1] if i >= 1 else float("nan") for i in range(len(calendar))]

            fig, axes = plt.subplots(3, 1, figsize=(12.5, 11.2), sharex=True)
            ax_amt, ax_yoy, ax_qoq = axes
            if q_rev.notna().any():
                ax_amt.bar(qx, q_rev, color="#4C78A8", alpha=0.75, label="单季营业收入")
            ax_amt.set_ylabel("单季营收（亿元）")
            ax_np = ax_amt.twinx()
            if q_np.notna().any():
                ax_np.plot(qx, q_np, color="#F58518", marker="o", linewidth=1.8, label="单季归母净利")
            ax_np.axhline(0, color="#666666", linewidth=0.8)
            ax_np.set_ylabel("单季归母净利（亿元）")
            ax_amt.set_title(f"{title_prefix} 单季营收与净利（累计拆分）", fontsize=13, pad=10)
            ax_amt.grid(True, linestyle="--", alpha=0.35)
            h1, l1 = ax_amt.get_legend_handles_labels()
            h2, l2 = ax_np.get_legend_handles_labels()
            ax_amt.legend(h1 + h2, l1 + l2, loc="best", fontsize=9)

            ylim_yoy = _expand_pct_ylim(
                _yoy_view_limit(q_rev_yoy, q_np_yoy), q_rev_yoy, q_rev_amt.tolist(), rev_yoy_prev
            )
            ylim_yoy = _expand_pct_ylim(ylim_yoy, q_np_yoy, q_np_amt.tolist(), np_yoy_prev)
            clipped_yoy = _draw_clipped_pct_lines(
                ax_yoy,
                qx,
                [
                    (q_rev_yoy, "单季营收同比", "#4C78A8", q_rev_amt.tolist(), rev_yoy_prev),
                    (q_np_yoy, "单季净利同比", "#F58518", q_np_amt.tolist(), np_yoy_prev),
                ],
                ylim_yoy,
            )
            ax_yoy.axhline(0, color="#666666", linewidth=0.8)
            ax_yoy.set_ylim(-ylim_yoy * 1.18, ylim_yoy * 1.22)
            ax_yoy.set_ylabel("单季同比（%）")
            ax_yoy.grid(True, linestyle="--", alpha=0.35)
            ax_yoy.legend(loc="best", fontsize=9)
            ax_yoy.set_title("相对历年同季（单季同比）", fontsize=11, pad=6)

            ylim_qoq = _expand_pct_ylim(
                _yoy_view_limit(q_rev_qoq, q_np_qoq), q_rev_qoq, q_rev_amt.tolist(), rev_qoq_prev
            )
            ylim_qoq = _expand_pct_ylim(ylim_qoq, q_np_qoq, q_np_amt.tolist(), np_qoq_prev)
            clipped_qoq = _draw_clipped_pct_lines(
                ax_qoq,
                qx,
                [
                    (q_rev_qoq, "单季营收环比", "#4C78A8", q_rev_amt.tolist(), rev_qoq_prev),
                    (q_np_qoq, "单季净利环比", "#F58518", q_np_amt.tolist(), np_qoq_prev),
                ],
                ylim_qoq,
            )
            ax_qoq.axhline(0, color="#666666", linewidth=0.8)
            ax_qoq.set_ylim(-ylim_qoq * 1.18, ylim_qoq * 1.22)
            ax_qoq.set_ylabel("单季环比（%）")
            ax_qoq.set_xticks(qx)
            ax_qoq.set_xticklabels(q_labels, rotation=55, ha="right", fontsize=8)
            ax_qoq.grid(True, linestyle="--", alpha=0.35)
            ax_qoq.legend(loc="best", fontsize=9)
            ax_qoq.set_title("相对上一自然季（单季环比，含四季→次年一季）", fontsize=11, pad=6)
            note = "口径：Q1=一季报，Q2=半年报−一季报，Q3=三季报−半年报，Q4=年报−三季报；缺期留空。上图柱/线看各季绝对水平。"
            if clipped_yoy or clipped_qoq:
                note += " 百分比极端值已截断（三角），CSV 保留原值。"
            ax_qoq.text(0.01, 0.02, note, transform=ax_qoq.transAxes, fontsize=8, color="#444444")
            charts.append(("单季营收与净利", _save_fig(fig, out_dir / "单季营收与净利.png")))
            plt.close(fig)

    return charts


def _fmt_yi(value_yuan) -> str:
    if value_yuan is None or pd.isna(value_yuan):
        return "—"
    return f"{float(value_yuan) / 1e8:.2f} 亿元"


def _fmt_pct(value, plus: bool = True) -> str:
    if value is None or pd.isna(value):
        return "—"
    number = float(value)
    sign = "+" if plus and number > 0 else ""
    return f"{sign}{number:.1f}%"


def _fmt_num(value, unit: str = "", digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.{digits}f}{unit}"


FINANCE_CHART_FILES = (
    "营收与净利润趋势.png",
    "利润率与ROE趋势.png",
    "经营现金流vs净利润.png",
    "营收净利同比增速.png",
    "单季营收与净利.png",
)
FINANCE_BUNDLE_FILES = ("财务分析.md", "财务数据.csv", *FINANCE_CHART_FILES)

_DATE_AJAX = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/lrbDateAjaxNew"
_PDF_PERIODS = (
    (re.compile(r"(20\d{2})\s*年\s*(?:年度报告|年报)"), "{0}-12-31"),
    (re.compile(r"(20\d{2})\s*年\s*(?:第三季度|三季报)"), "{0}-09-30"),
    (re.compile(r"(20\d{2})\s*年\s*(?:半年度|半年报)"), "{0}-06-30"),
    (re.compile(r"(20\d{2})\s*年\s*(?:第一季度|一季报)"), "{0}-03-31"),
)


def finance_csv_path(stock_dir: Path) -> Path:
    return Path(stock_dir) / FINANCE_DIR_NAME / "财务数据.csv"


def finance_out_dir(stock_dir: Path) -> Path:
    return Path(stock_dir) / FINANCE_DIR_NAME


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    sep = "|" + " --- |" * len(headers)
    if not rows:
        rows = [["—"] * len(headers)]
    body = ["| " + " | ".join("" if v is None else str(v) for v in row) + " |" for row in rows]
    return "\n".join([head, sep, *body])


def local_latest_period(stock_dir: Path) -> str:
    """本地财务数据.csv 的最新报告期；没有则尝试从财报 PDF 标题推断。"""
    csv_path = finance_csv_path(stock_dir)
    if csv_path.is_file():
        try:
            df = pd.read_csv(csv_path, encoding="utf-8-sig")
        except Exception:
            df = pd.DataFrame()
        if not df.empty and "报告期" in df.columns:
            dates = [_norm_date(v) for v in df["报告期"]]
            dates = [d for d in dates if d]
            if dates:
                return max(dates)
    return _latest_period_from_pdfs(stock_dir)


def _latest_period_from_pdfs(stock_dir: Path) -> str:
    folder = Path(stock_dir) / "财报"
    if not folder.is_dir():
        return ""
    found: list[str] = []
    for path in folder.glob("*.pdf"):
        for pattern, tmpl in _PDF_PERIODS:
            match = pattern.search(path.name)
            if match:
                year = next(g for g in match.groups() if g)
                found.append(tmpl.format(year))
                break
    return max(found) if found else ""


def fetch_remote_latest_period(code: str) -> str:
    """只问「当前最新报告期是哪一期」，不拉全表。"""
    import requests

    symbol = em_f10_symbol(code)
    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    )
    for company_type in ("4", "3", "1", "2"):
        try:
            resp = session.get(
                _DATE_AJAX,
                params={"companyType": company_type, "reportDateType": "0", "code": symbol},
                timeout=15,
            )
            data = resp.json() if resp.ok else {}
            rows = data.get("data") or []
            dates = [_norm_date(item.get("REPORT_DATE")) for item in rows if isinstance(item, dict)]
            dates = [d for d in dates if d]
            if dates:
                latest = max(dates)
                log.info("  远端最新报告期 %s = %s", code, latest)
                return latest
        except Exception as exc:
            log.debug("  远端报告期查询失败 %s type=%s：%s", code, company_type, exc)
    return ""


def load_local_detail(csv_path: Path) -> pd.DataFrame:
    """从已导出的财务数据.csv 还原作图所需的明细（含 _raw 列）。"""
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    if df.empty or "报告期" not in df.columns:
        return pd.DataFrame()
    df["报告期"] = df["报告期"].map(_norm_date)
    df = df[df["报告期"] != ""].sort_values("报告期", ascending=True).reset_index(drop=True)
    raw_map = {
        "营业收入": ("营业收入(万元)", 1e4),
        "归母净利润": ("归母净利润(万元)", 1e4),
        "扣非净利润": ("扣非净利润(万元)", 1e4),
        "经营现金流净额": ("经营现金流净额(万元)", 1e4),
        "毛利率": ("毛利率(%)", 1.0),
        "净利率": ("净利率(%)", 1.0),
        "ROE": ("ROE(%)", 1.0),
        "资产负债率": ("资产负债率(%)", 1.0),
        "流动比率": ("流动比率", 1.0),
        "基本每股收益": ("基本每股收益(元)", 1.0),
    }
    for metric, (col, scale) in raw_map.items():
        dest = f"_{metric}_raw"
        if dest in df.columns:
            continue
        if col in df.columns:
            df[dest] = pd.to_numeric(df[col], errors="coerce") * scale
        else:
            df[dest] = float("nan")
    return df


def _missing_from_detail(detail: pd.DataFrame) -> list[str]:
    missing: list[str] = []
    for name in REQUIRED_METRICS:
        col = f"_{name}_raw"
        if col not in detail.columns or not pd.to_numeric(detail[col], errors="coerce").notna().any():
            missing.append(name)
    return missing


def write_finance_outputs(code: str, name: str, detail: pd.DataFrame, missing: list[str], out_dir: Path) -> list[tuple[str, Path]]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    detail = _add_quarterly_columns(detail)
    _export_csv(detail, out_dir / "财务数据.csv")
    charts = _plot_charts(detail, code, name, out_dir)
    _write_markdown(out_dir / "财务分析.md", code, name, detail, missing, charts)
    return charts


def _maybe_fetch_latest_pdf(code: str, name: str, stock_dir: Path, cfg: dict | None) -> None:
    if not cfg:
        return
    try:
        from datetime import date, timedelta

        from stock_screener.config import cfg_get
        from stock_screener.datasources import cninfo as cninfo_mod
        from stock_screener.datasources.cninfo import CninfoClient
        from stock_screener.downloader import HttpClient, sanitize_filename

        if not cfg_get(cfg, "downloads.financial_reports.enabled", True):
            return
        types = cfg_get(cfg, "downloads.financial_reports.types", ["年报", "半年报", "一季报", "三季报"])
        http = HttpClient(
            rate_limit_seconds=cfg_get(cfg, "network.rate_limit_seconds", 1.5),
            max_retries=min(int(cfg_get(cfg, "network.max_retries", 3)), 2),
            timeout_seconds=cfg_get(cfg, "network.timeout_seconds", 30),
            manifest_path=cfg_get(cfg, "paths.manifest", "data/manifest.json"),
            use_system_proxy=cfg_get(cfg, "network.use_system_proxy", False),
        )
        cn = CninfoClient(http, cache_dir=cfg_get(cfg, "paths.cache_dir", "data/cache"))
        since = (date.today() - timedelta(days=550)).isoformat()
        reports = cn.periodic_reports(code, types, since=since)
        if not reports:
            return
        latest = reports[0]
        dest = Path(stock_dir) / "财报" / f"{latest['date']}_{sanitize_filename(latest['title'])}.pdf"
        status = http.download_pdf(latest["url"], dest, referer=cninfo_mod.REFERER)
        if status == "ok":
            log.info("  已补最新定期报告 PDF：%s %s", code, dest.name)
        elif status == "skip":
            log.info("  最新定期报告 PDF 已存在：%s", dest.name)
    except Exception as exc:
        log.warning("  补最新定期报告 PDF 失败 %s %s：%s", code, name, exc)


def _write_markdown(
    path: Path,
    code: str,
    name: str,
    detail: pd.DataFrame,
    missing: list[str],
    charts: list[tuple[str, Path]],
) -> None:
    latest = detail.iloc[-1]
    period = latest["报告期"]
    ptype = latest.get("报告类型") or _period_type(str(period))
    last_idx = len(detail) - 1

    def _yoy_text(pct_col: str, amount_col: str, raw_col: str) -> str:
        pct = latest.get(pct_col)
        current = latest.get(raw_col)
        previous = _prior_same_period(detail, last_idx, amount_col)
        # amount_col 是万元，_prior 返回万元；raw 是元
        prev_yuan = previous * 1e4 if pd.notna(previous) else float("nan")
        text = _fmt_pct(pct)
        if _yoy_is_low_base(current, prev_yuan, pct):
            text += f"（低基数，上年同期 {_fmt_yi(prev_yuan)}，不宜直接读百分比）"
        return text

    lines = [
        f"# {name}（{code}）财务分析",
        "",
        "- 数据来源：东方财富 F10 公开财报（利润表/资产负债表/现金流量表 + 财务分析主要指标），与披露定期报告同一口径；**未解析**巨潮扫描版 PDF。",
        f"- 覆盖报告期：{detail['报告期'].iloc[0]} ～ {detail['报告期'].iloc[-1]}，共 {len(detail)} 期（累计口径：一季报/半年报/三季报/年报）。",
        "- 同比：与上年同期比（年报对年报、一季报对一季报）。上年同期绝对值过小或由亏转盈时，百分比仅作参考，图上不连线。",
        "- 环比（累计）：与**相邻报告期**比（年报→一季报、三季报→年报为累计口径，不可直接当作单季环比）。",
        "- 单季：一季=一季报；二季=半年报−一季报；三季=三季报−半年报；四季=年报−三季报。单季同比=历年同季；单季环比=上一自然季（含四季→次年一季）。",
        f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"## 最近一期要点（{period} {ptype}）",
        "",
        f"- 营业收入 {_fmt_yi(latest.get('_营业收入_raw'))}，同比 {_yoy_text('营业收入同比(%)', '营业收入(万元)', '_营业收入_raw')}，环比 {_fmt_pct(latest.get('营业收入环比(%)'))}",
        f"- 归母净利润 {_fmt_yi(latest.get('_归母净利润_raw'))}，同比 {_yoy_text('归母净利润同比(%)', '归母净利润(万元)', '_归母净利润_raw')}，环比 {_fmt_pct(latest.get('归母净利润环比(%)'))}",
        f"- 扣非净利润 {_fmt_yi(latest.get('_扣非净利润_raw'))}，同比 {_yoy_text('扣非净利润同比(%)', '扣非净利润(万元)', '_扣非净利润_raw')}，环比 {_fmt_pct(latest.get('扣非净利润环比(%)'))}",
        f"- 毛利率 {_fmt_num(latest.get('毛利率(%)'), '%', 1)}，净利率 {_fmt_num(latest.get('净利率(%)'), '%', 1)}，ROE {_fmt_num(latest.get('ROE(%)'), '%', 1)}",
        f"- 经营现金流净额 {_fmt_yi(latest.get('_经营现金流净额_raw'))}，同比 {_fmt_pct(latest.get('经营现金流净额同比(%)'))}，环比 {_fmt_pct(latest.get('经营现金流净额环比(%)'))}",
        f"- 资产负债率 {_fmt_num(latest.get('资产负债率(%)'), '%', 1)}，流动比率 {_fmt_num(latest.get('流动比率'), '', 2)}",
        f"- 基本每股收益 {_fmt_num(latest.get('基本每股收益(元)'), ' 元', 2)}，同比 {_fmt_pct(latest.get('基本每股收益同比(%)'))}",
        f"- 单季营业收入 {_fmt_yi(latest.get('_营业收入_单季_raw'))}，同比 {_fmt_pct(latest.get('单季营业收入同比(%)'))}，环比 {_fmt_pct(latest.get('单季营业收入环比(%)'))}",
        f"- 单季归母净利润 {_fmt_yi(latest.get('_归母净利润_单季_raw'))}，同比 {_yoy_text('单季归母净利润同比(%)', '单季归母净利润(万元)', '_归母净利润_单季_raw')}，环比 {_fmt_pct(latest.get('单季归母净利润环比(%)'))}",
        "",
    ]
    if missing:
        lines += [
            "## 未取到的指标",
            "",
            "以下指标在公开接口各报告期均缺失，已跳过：" + "、".join(missing) + "。",
            "",
        ]
    if charts:
        lines += ["## 趋势图", ""]
        for title, img in charts:
            lines.append(f"![{title}]({img.name})")
            lines.append("")
        if any(title == "营收净利同比增速" for title, _ in charts):
            lines += [
                "同比增速图对超出可视窗口的点不连线，并标注「低基数」或实际百分比，避免单期爆炸压扁历史波动。CSV 中仍保留原计算值。",
                "",
            ]
        if any(title == "单季营收与净利" for title, _ in charts):
            lines += [
                "单季图覆盖全部能从累计数拆出的历史季度（Q1=一季报，Q2=半年报−一季报，Q3=三季报−半年报，Q4=年报−三季报；缺期留空），不是只画最近几季。上方面板看各季绝对水平，中间/下方分别是历年同季同比与上一自然季环比。",
                "",
            ]
    hist_cols = [
        c
        for c in (
            "报告期",
            "报告类型",
            "营业收入(万元)",
            "营业收入同比(%)",
            "营业收入环比(%)",
            "归母净利润(万元)",
            "归母净利润同比(%)",
            "归母净利润环比(%)",
            "单季营业收入(万元)",
            "单季归母净利润(万元)",
            "单季归母净利润同比(%)",
            "单季归母净利润环比(%)",
            "毛利率(%)",
            "净利率(%)",
            "ROE(%)",
            "经营现金流净额(万元)",
            "经营现金流净额同比(%)",
        )
        if c in detail.columns
    ]
    hist = detail[[c for c in hist_cols if c in detail.columns]].copy()
    hist = hist.sort_values("报告期", ascending=False)
    hist_rows = []
    for row in hist.itertuples(index=False):
        cells = []
        for col, val in zip(hist.columns, row):
            if col in {"报告期", "报告类型"}:
                cells.append("" if val is None or (isinstance(val, float) and pd.isna(val)) else str(val))
            elif "同比" in col or "环比" in col:
                cells.append(_fmt_pct(val))
            elif col.endswith("(万元)"):
                cells.append("—" if val is None or pd.isna(val) else f"{float(val):.2f}")
            elif col.endswith("(%)"):
                cells.append(_fmt_num(val, "%", 1))
            else:
                cells.append("" if val is None or (isinstance(val, float) and pd.isna(val)) else str(val))
        hist_rows.append(cells)
    lines += [
        "## 历史多期",
        "",
        _md_table(list(hist.columns), hist_rows),
        "",
        "金额为万元；同比/环比为%。完整列见同目录 [财务数据.csv](财务数据.csv)。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _export_csv(detail: pd.DataFrame, path: Path) -> None:
    export = detail[[c for c in detail.columns if not str(c).startswith("_")]].copy()
    # 新到旧，便于阅读
    export = export.sort_values("报告期", ascending=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    export.to_csv(path, index=False, encoding="utf-8-sig", float_format="%.2f")


def analyze_stock_finance(code: str, name: str, stock_dir: Path, cfg: dict | None = None) -> bool:
    """本地报告期已跟上远端则直接用 CSV 出图；否则再拉全表。成功返回 True。"""
    code = str(code).zfill(6)
    stock_dir = Path(stock_dir)
    out_dir = finance_out_dir(stock_dir)
    csv_path = finance_csv_path(stock_dir)
    local_period = local_latest_period(stock_dir)
    remote_period = fetch_remote_latest_period(code)

    use_local = False
    if csv_path.is_file() and local_period:
        if remote_period and local_period >= remote_period:
            use_local = True
            log.info("  本地财报已是最新，跳过下载（%s 本地 %s ≥ 远端 %s）", code, local_period, remote_period)
        elif not remote_period:
            use_local = True
            log.info("  远端报告期查询失败，使用本地财报出图（%s 本地 %s）", code, local_period)

    if use_local:
        try:
            detail = load_local_detail(csv_path)
        except Exception as exc:
            log.warning("  读取本地财务 CSV 失败，改为联网拉取：%s", exc)
            detail = pd.DataFrame()
        if not detail.empty:
            missing = _missing_from_detail(detail)
            charts = write_finance_outputs(code, name, detail, missing, out_dir)
            log.info(
                "  财务分析（本地）：%s %s，%d 个报告期，图表 %d 张 → %s",
                code, name, len(detail), len(charts), out_dir.resolve(),
            )
            return True

    log.info("  财务分析：拉取东方财富公开财报（%s）", em_f10_symbol(code))
    frames = _fetch_frames(code)
    if all(df is None for df in frames.values()):
        if csv_path.is_file():
            log.warning("  联网拉取失败，回退本地 CSV：%s", code)
            detail = load_local_detail(csv_path)
            if not detail.empty:
                write_finance_outputs(code, name, detail, _missing_from_detail(detail), out_dir)
                return True
        log.error("  财务分析失败：%s %s 所有公开财报接口均无数据", code, name)
        return False
    detail, missing = _extract_metrics(frames)
    if detail.empty:
        log.error("  财务分析失败：%s %s 未能解析出任何报告期", code, name)
        return False
    charts = write_finance_outputs(code, name, detail, missing, out_dir)
    _maybe_fetch_latest_pdf(code, name, stock_dir, cfg)
    miss_note = f"；未取到：{'、'.join(missing)}" if missing else ""
    log.info(
        "  财务分析完成：%s %s，%d 个报告期，图表 %d 张 → %s%s",
        code, name, len(detail), len(charts), out_dir.resolve(), miss_note,
    )
    return True
