"""成长股量化筛选：① 业绩增速 ④ 市值上限 ⑤ 机构持仓适中。

②（估值低位）③（增长动因）⑥（产业趋势）由使用者主观判断，程序不做处理。
"""

from __future__ import annotations

import logging

import pandas as pd

from .config import cfg_get
from .datasources import market

log = logging.getLogger(__name__)

BSE_PREFIXES = ("4", "8", "9")


def _find_col(df: pd.DataFrame, *keywords: str) -> str | None:
    """优先精确匹配首个关键词，否则返回同时包含全部关键词的首个列名。"""
    for col in df.columns:
        if str(col) == keywords[0]:
            return col
    for col in df.columns:
        if all(k in str(col) for k in keywords):
            return col
    return None


def _board(code: str) -> str:
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("300", "301", "302")):
        return "创业板"
    if code.startswith("6"):
        return "沪主板"
    if code.startswith("0"):
        return "深主板"
    return "北交所"


def _filter_market_cap(cfg: dict) -> pd.DataFrame | None:
    """④ 市值上限 + 剔除ST/退市整理股（默认不含北交所）。"""
    spot = market.get_spot()
    if spot is None:
        log.error("无法获取全A行情快照，筛选中止")
        return None
    cap_max_yi = float(cfg_get(cfg, "screening.market_cap_max_yi", 200))
    df = spot.dropna(subset=["总市值"]).copy()
    total = len(df)
    include_bse = bool(cfg_get(cfg, "screening.include_bse", False))
    if not include_bse:
        df = df[~df["代码"].str.startswith(BSE_PREFIXES)]
    if cfg_get(cfg, "screening.exclude_st", True):
        df = df[~df["名称"].str.contains("ST|退", na=False)]
    df = df[df["总市值"] < cap_max_yi * 1e8]
    log.info(
        "④ 基础过滤：全A %d -> %d 只（总市值<%.0f亿、剔除ST/退市%s）",
        total, len(df), cap_max_yi, "" if include_bse else "、不含北交所",
    )
    return df


def _collect_growth(cfg: dict, candidates: set[str]) -> dict[str, list[dict]]:
    """按报告期由新到旧收集每只候选股最近的营收/净利同比记录。"""
    need = int(cfg_get(cfg, "screening.growth.consecutive_periods", 1))
    lookback = int(cfg_get(cfg, "screening.growth.periods_lookback", 6))
    records: dict[str, list[dict]] = {c: [] for c in candidates}
    pending = set(candidates)
    for period in market.recent_report_periods(lookback):
        if not pending:
            break
        perf = market.get_performance(period)
        if perf is None:
            continue
        # 兼容不同版本 akshare 的列名（营业收入/营业总收入）
        rev_col = _find_col(perf, "营业总收入-同比增长") or _find_col(perf, "营业", "同比")
        profit_col = _find_col(perf, "净利润-同比增长") or _find_col(perf, "净利润", "同比")
        industry_col = _find_col(perf, "所处行业")
        if not rev_col or not profit_col:
            log.warning("业绩报表 %s 缺少同比增长列，跳过该期", period)
            continue
        sub = perf[perf["股票代码"].isin(pending)]
        industries = (
            sub[industry_col] if industry_col else pd.Series([""] * len(sub), index=sub.index)
        )
        for code, rev, profit, industry in zip(
            sub["股票代码"],
            pd.to_numeric(sub[rev_col], errors="coerce"),
            pd.to_numeric(sub[profit_col], errors="coerce"),
            industries,
        ):
            recs = records[code]
            if len(recs) >= need or (recs and recs[-1]["period"] == period):
                continue
            recs.append({"period": period, "rev": rev, "profit": profit, "industry": industry})
            if len(recs) >= need:
                pending.discard(code)
        log.info(
            "业绩报表 %s：%d/%d 只候选已取到足够报告期",
            period, len(candidates) - len(pending), len(candidates),
        )
    return records


def _apply_growth_filter(cfg: dict, records: dict[str, list[dict]]) -> dict[str, dict]:
    """① 最新 N 期营收与净利同比均不低于阈值。"""
    rev_min = float(cfg_get(cfg, "screening.growth.revenue_yoy_min", 40))
    profit_min = float(cfg_get(cfg, "screening.growth.profit_yoy_min", 40))
    need = int(cfg_get(cfg, "screening.growth.consecutive_periods", 1))
    passed: dict[str, dict] = {}
    for code, recs in records.items():
        if len(recs) < need:
            continue
        checked = recs[:need]
        if all(
            pd.notna(r["rev"]) and pd.notna(r["profit"])
            and r["rev"] >= rev_min and r["profit"] >= profit_min
            for r in checked
        ):
            passed[code] = recs[0]
    log.info(
        "① 业绩增速过滤：%d -> %d 只（最新%d期营收同比>=%.0f%%且净利同比>=%.0f%%）",
        len(records), len(passed), need, rev_min, profit_min,
    )
    return passed


def _collect_institutions(cfg: dict, spot: pd.DataFrame) -> tuple[dict[str, dict], str]:
    """获取每只股票最新可得的机构持仓（家数、占流通股比例）。

    使用东方财富基金持仓；占比按 持股总数/流通股本 估算（流通股本 = 流通市值/最新价）。
    """
    quarters_lb = int(cfg_get(cfg, "screening.institution.quarters_lookback", 2))
    inst_map: dict[str, dict] = {}
    source = ""

    log.info("机构持仓使用东方财富基金持仓（占比按流通股本估算）")
    float_shares: dict[str, float] = {}
    for code, mv, price in zip(
        spot["代码"],
        pd.to_numeric(spot["流通市值"], errors="coerce"),
        pd.to_numeric(spot["最新价"], errors="coerce"),
    ):
        if pd.notna(mv) and pd.notna(price) and price > 0:
            float_shares[code] = mv / price
    for period in market.recent_report_periods(quarters_lb):
        dff = market.get_fund_hold(period)
        if dff is None:
            continue
        num_col = _find_col(dff, "持有基金家数")
        shares_col = _find_col(dff, "持股总数")
        if not num_col or not shares_col:
            continue
        source = "东方财富基金持仓(占比估算)"
        for code, num, shares in zip(
            dff["股票代码"],
            pd.to_numeric(dff[num_col], errors="coerce"),
            pd.to_numeric(dff[shares_col], errors="coerce"),
        ):
            if code in inst_map:
                continue
            fs = float_shares.get(code)
            ratio = shares / fs * 100 if fs and pd.notna(shares) else float("nan")
            inst_map[code] = {"num": num, "ratio": ratio, "quarter": period}
    return inst_map, source


def run_screening(cfg: dict) -> pd.DataFrame | None:
    """执行①④⑤三步过滤，返回入选股票清单（DataFrame）。失败返回 None。"""
    base = _filter_market_cap(cfg)
    if base is None:
        return None
    candidates = set(base["代码"])
    if not candidates:
        log.warning("市值过滤后无候选股票")
        return pd.DataFrame()

    growth_pass = _apply_growth_filter(cfg, _collect_growth(cfg, candidates))
    if not growth_pass:
        return pd.DataFrame()

    inst_map, source = _collect_institutions(cfg, base)
    min_inst = int(cfg_get(cfg, "screening.institution.min_institutions", 1))
    max_ratio = float(cfg_get(cfg, "screening.institution.max_float_ratio_pct", 20))

    spot_map = base.set_index("代码")
    rows = []
    for code, rec in growth_pass.items():
        inst = inst_map.get(code)
        num = inst["num"] if inst is not None and pd.notna(inst["num"]) else 0
        ratio = inst["ratio"] if inst is not None else float("nan")
        if num < min_inst:
            continue
        if pd.notna(ratio) and ratio > max_ratio:
            continue
        srow = spot_map.loc[code]
        rows.append({
            "代码": code,
            "名称": srow["名称"],
            "板块": _board(code),
            "所处行业": rec.get("industry", ""),
            "总市值(亿)": round(float(srow["总市值"]) / 1e8, 1),
            "报告期": rec["period"],
            "营收同比(%)": round(float(rec["rev"]), 1),
            "净利同比(%)": round(float(rec["profit"]), 1),
            "机构家数": int(num),
            "机构占流通股比(%)": round(float(ratio), 2) if pd.notna(ratio) else "",
            "机构数据期": inst["quarter"] if inst is not None else "",
        })
    log.info(
        "⑤ 机构持仓过滤：%d -> %d 只（机构家数>=%d 且占流通股比<=%.0f%%，数据源：%s）",
        len(growth_pass), len(rows), min_inst, max_ratio, source or "无可用数据",
    )
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values("净利同比(%)", ascending=False).reset_index(drop=True)
    return result
