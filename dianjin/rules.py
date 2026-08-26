"""点金术筛选的纯函数规则（不访问网络，便于单测）。"""

from __future__ import annotations

import math
from typing import Any

BSE_PREFIXES = ("4", "8", "9")

DEFAULTS = {
    "dividend_min": 3.0,
    "extra_dividend_min": 4.0,
    "pe_max": 20.0,
    "ma120_ratio": 0.88,
    "extra_ma120_ratio": 0.82,
    "ma_period": 120,
    "exclude_st": True,
    "include_bse": False,
    "recent_signal_days": 5,
    "kline_rate_limit": 0.25,
}


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "None", "nan", "NaN"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def is_st_or_delist(name: str) -> bool:
    text = str(name or "")
    return "ST" in text or "退" in text


def is_bse(code: str) -> bool:
    return str(code).zfill(6).startswith(BSE_PREFIXES)


def passes_pe(pe_dyn: Any, pe_static: Any, pe_ttm: Any, pe_max: float = 20.0) -> bool:
    """三档市盈率均须为正且严格小于 pe_max。亏损、缺失、非数字视为不满足。"""
    for raw in (pe_dyn, pe_static, pe_ttm):
        value = to_float(raw)
        if value is None or value <= 0 or value >= pe_max:
            return False
    return True


def passes_dividend(div: Any, min_pct: float) -> bool:
    """股息率须严格大于门槛。缺失视为不满足。"""
    value = to_float(div)
    return value is not None and value > min_pct


def passes_ma_discount(close: Any, ma120: Any, ratio: float) -> bool:
    """收盘价严格小于 MA120 × ratio。"""
    price = to_float(close)
    mean = to_float(ma120)
    if price is None or mean is None or mean <= 0:
        return False
    return price < mean * ratio


def close_over_ma(close: Any, ma120: Any) -> float | None:
    price = to_float(close)
    mean = to_float(ma120)
    if price is None or mean is None or mean <= 0:
        return None
    return price / mean


def passes_universe(
    code: str,
    name: str,
    *,
    exclude_st: bool = True,
    include_bse: bool = False,
) -> bool:
    if exclude_st and is_st_or_delist(name):
        return False
    if not include_bse and is_bse(code):
        return False
    return True


def passes_value(
    dividend: Any,
    pe_dyn: Any,
    pe_static: Any,
    pe_ttm: Any,
    dividend_min: float = 3.0,
    pe_max: float = 20.0,
) -> bool:
    """快照阶段：只看股息 + 三档市盈率，不拉 K 线。"""
    return passes_pe(pe_dyn, pe_static, pe_ttm, pe_max) and passes_dividend(
        dividend, dividend_min
    )


def classify_lists(
    *,
    dividend: Any,
    pe_dyn: Any,
    pe_static: Any,
    pe_ttm: Any,
    close: Any,
    ma120: Any,
    dividend_min: float = 3.0,
    extra_dividend_min: float = 4.0,
    pe_max: float = 20.0,
    ma120_ratio: float = 0.88,
    extra_ma120_ratio: float = 0.82,
) -> tuple[bool, bool]:
    """返回 (是否点金术, 是否 extra)。extra 是点金术的加严子集。"""
    if not passes_pe(pe_dyn, pe_static, pe_ttm, pe_max):
        return False, False
    if not passes_dividend(dividend, dividend_min):
        return False, False
    if not passes_ma_discount(close, ma120, ma120_ratio):
        return False, False
    extra = passes_dividend(dividend, extra_dividend_min) and passes_ma_discount(
        close, ma120, extra_ma120_ratio
    )
    return True, extra
