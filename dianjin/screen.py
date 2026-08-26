"""快照 → 股息/PE → 仅对幸存者拉 MA120 → 点金术 / extra。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from dianjin.kline import fetch_close_and_ma120
from dianjin.rules import (
    DEFAULTS,
    classify_lists,
    close_over_ma,
    passes_universe,
    passes_value,
    to_float,
)
from dianjin.snapshot import (
    SnapshotError,
    fetch_market_snapshot,
    fill_missing_valuation,
    valuation_gappy,
)

log = logging.getLogger("dianjin")


@dataclass
class DianjinHit:
    code: str
    name: str
    close: float
    ma120: float
    close_ma_ratio: float
    dividend: float
    pe_dyn: float
    pe_static: float
    pe_ttm: float
    is_extra: bool
    bars: int = 0


@dataclass
class ScreenResult:
    snapshot_count: int = 0
    after_universe: int = 0
    value_pass: int = 0
    ma_fetched: int = 0
    hits: list[DianjinHit] = field(default_factory=list)
    extra: list[DianjinHit] = field(default_factory=list)
    error: str = ""
    snapshot_ok: bool = True
    hist_limited: bool = False
    elapsed_seconds: float = 0.0
    snapshot_second_filled: int = 0
    snapshot_second_missing: int = 0
    ma_second_filled: int = 0
    ma_second_missing: int = 0


def _cfg(cfg: dict[str, Any] | None, key: str, default: Any) -> Any:
    block = (cfg or {}).get("dianjin") if isinstance(cfg, dict) else None
    if isinstance(block, dict) and key in block:
        return block[key]
    return DEFAULTS.get(key, default)


def apply_universe(rows: list[dict[str, Any]], cfg: dict[str, Any] | None) -> list[dict[str, Any]]:
    exclude_st = bool(_cfg(cfg, "exclude_st", True))
    include_bse = bool(_cfg(cfg, "include_bse", False))
    return [
        row
        for row in rows
        if passes_universe(
            str(row.get("code") or ""),
            str(row.get("name") or ""),
            exclude_st=exclude_st,
            include_bse=include_bse,
        )
    ]


def apply_value_filter(rows: list[dict[str, Any]], cfg: dict[str, Any] | None) -> list[dict[str, Any]]:
    dividend_min = float(_cfg(cfg, "dividend_min", 3.0))
    pe_max = float(_cfg(cfg, "pe_max", 20.0))
    passed: list[dict[str, Any]] = []
    for row in rows:
        if passes_value(
            row.get("dividend"),
            row.get("pe_dyn"),
            row.get("pe_static"),
            row.get("pe_ttm"),
            dividend_min=dividend_min,
            pe_max=pe_max,
        ):
            passed.append(row)
    return passed


def _sort_hits(hits: list[DianjinHit]) -> list[DianjinHit]:
    return sorted(hits, key=lambda h: (-h.dividend, h.close_ma_ratio, h.code))


def screen_market(
    cfg: dict[str, Any] | None = None,
    *,
    codes: list[str] | None = None,
    hist_limit: int | None = None,
    snapshot_rows: list[dict[str, Any]] | None = None,
) -> ScreenResult:
    """生产路径：全市场快照 + 全部股息/PE 幸存者的 MA120。hist_limit 仅测试用。"""
    t0 = time.monotonic()
    result = ScreenResult()
    try:
        rows = snapshot_rows
        if rows is None:
            page_size = int(_cfg(cfg, "snapshot_page_size", 100))
            sleep_s = float(_cfg(cfg, "snapshot_sleep", 0.15))
            rows = fetch_market_snapshot(page_size=page_size, sleep_seconds=sleep_s)
        result.snapshot_count = len(rows)
        log.info("中间计数：全市场快照 %d", result.snapshot_count)
    except SnapshotError as exc:
        result.snapshot_ok = False
        result.error = f"全市场估值快照失败（未放宽市盈率规则）：{exc}"
        log.error("%s", result.error)
        result.elapsed_seconds = time.monotonic() - t0
        return result
    except Exception as exc:
        result.snapshot_ok = False
        result.error = f"全市场估值快照失败（未放宽市盈率规则）：{exc}"
        log.exception("%s", result.error)
        result.elapsed_seconds = time.monotonic() - t0
        return result

    if codes:
        wanted = {str(c).zfill(6) for c in codes if str(c).strip()}
        rows = [row for row in rows if row.get("code") in wanted]
        log.info("中间计数：--codes 过滤后 %d", len(rows))

    rows = apply_universe(rows, cfg)
    result.after_universe = len(rows)
    log.info("中间计数：剔除 ST/退市%s 后 %d", "（默认不含北交所）", result.after_universe)

    need_val = [row for row in rows if valuation_gappy(row)]
    if need_val:
        filled, still = fill_missing_valuation(need_val)
        result.snapshot_second_filled = filled
        result.snapshot_second_missing = still

    # 同花顺股息率TTM：只对已过三档 PE 的股票拉 F10 分红表覆盖 dividend。
    # 注入 snapshot_rows 的测试默认不打网；生产全市场快照默认覆盖。
    do_overlay = bool(_cfg(cfg, "ths_ttm_overlay", snapshot_rows is None and hist_limit is None))
    if do_overlay:
        from dianjin.rules import passes_pe
        from dianjin.ths_yield import overlay_ths_ttm

        pe_max = float(_cfg(cfg, "pe_max", 20.0))
        pe_rows = [
            row
            for row in rows
            if passes_pe(row.get("pe_dyn"), row.get("pe_static"), row.get("pe_ttm"), pe_max)
        ]
        log.info("同花顺股息率TTM：对 %d 只已过市盈率的股票覆盖 F10 分红表", len(pe_rows))
        overlay_ths_ttm(pe_rows)

    value_rows = apply_value_filter(rows, cfg)
    result.value_pass = len(value_rows)
    log.info("中间计数：股息+三档市盈率通过 %d（尚未拉 K 线）", result.value_pass)

    if hist_limit is not None and int(hist_limit) >= 0:
        cap = int(hist_limit)
        if len(value_rows) > cap:
            log.warning(
                "测试用 --hist-limit=%d，仅对前 %d 只拉 MA120（生产日报不得使用）",
                cap,
                cap,
            )
            value_rows = value_rows[:cap]
            result.hist_limited = True

    period = int(_cfg(cfg, "ma_period", 120))
    rate = float(_cfg(cfg, "kline_rate_limit", 0.3))
    rate = min(max(rate, 0.25), 0.35)
    dividend_min = float(_cfg(cfg, "dividend_min", 3.0))
    extra_div = float(_cfg(cfg, "extra_dividend_min", 4.0))
    pe_max = float(_cfg(cfg, "pe_max", 20.0))
    ma_ratio = float(_cfg(cfg, "ma120_ratio", 0.88))
    extra_ratio = float(_cfg(cfg, "extra_ma120_ratio", 0.82))

    ma_map: dict[str, tuple[float | None, float | None, int]] = {}

    def _pull_ma(batch: list[dict[str, Any]], label: str) -> None:
        for i, row in enumerate(batch, 1):
            if result.ma_fetched > 0 or i > 1:
                time.sleep(rate)
            close, ma120, bars = fetch_close_and_ma120(row["code"], period=period)
            ma_map[row["code"]] = (close, ma120, bars)
            result.ma_fetched += 1
            if i % 25 == 0 or i == len(batch):
                log.info("MA120 %s进度 %d/%d", label, i, len(batch))

    _pull_ma(value_rows, "")
    incomplete = [
        row
        for row in value_rows
        if ma_map.get(row["code"], (None, None, 0))[1] is None
        or ma_map.get(row["code"], (None, None, 0))[2] < period
    ]
    if incomplete:
        log.info("MA120 二次确认：%d 只缺均线或根数不足，再拉一轮（腾讯优先）", len(incomplete))
        from dianjin.kline import clear_bar_cache

        for row in incomplete:
            ma_map.pop(row["code"], None)
            clear_bar_cache(row["code"])
        _pull_ma(incomplete, "二次确认")
        filled = sum(
            1
            for row in incomplete
            if ma_map.get(row["code"], (None, None, 0))[1] is not None
            and ma_map.get(row["code"], (None, None, 0))[2] >= period
        )
        result.ma_second_filled = filled
        result.ma_second_missing = len(incomplete) - filled
        log.info("MA120 二次确认：补全 %d，仍缺 %d", result.ma_second_filled, result.ma_second_missing)

    hits: list[DianjinHit] = []
    for row in value_rows:
        close, ma120, bars = ma_map.get(row["code"], (None, None, 0))
        if close is None or ma120 is None or bars < period:
            continue
        in_main, in_extra = classify_lists(
            dividend=row.get("dividend"),
            pe_dyn=row.get("pe_dyn"),
            pe_static=row.get("pe_static"),
            pe_ttm=row.get("pe_ttm"),
            close=close,
            ma120=ma120,
            dividend_min=dividend_min,
            extra_dividend_min=extra_div,
            pe_max=pe_max,
            ma120_ratio=ma_ratio,
            extra_ma120_ratio=extra_ratio,
        )
        if not in_main:
            continue
        ratio = close_over_ma(close, ma120) or 0.0
        hits.append(
            DianjinHit(
                code=row["code"],
                name=row.get("name") or row["code"],
                close=float(close),
                ma120=float(ma120),
                close_ma_ratio=float(ratio),
                dividend=float(to_float(row.get("dividend")) or 0.0),
                pe_dyn=float(to_float(row.get("pe_dyn")) or 0.0),
                pe_static=float(to_float(row.get("pe_static")) or 0.0),
                pe_ttm=float(to_float(row.get("pe_ttm")) or 0.0),
                is_extra=bool(in_extra),
                bars=int(bars),
            )
        )

    hits = _sort_hits(hits)
    extra = [h for h in hits if h.is_extra]
    result.hits = hits
    result.extra = extra
    result.elapsed_seconds = time.monotonic() - t0
    log.info(
        "中间计数：点金术 %d，点金术extra %d（extra ⊆ 点金术）；MA120 已拉 %d；耗时 %.1fs",
        len(hits),
        len(extra),
        result.ma_fetched,
        result.elapsed_seconds,
    )
    return result
