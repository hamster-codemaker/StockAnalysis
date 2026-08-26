"""自实现均线 / MACD / 布林 / RSI，不依赖 talib。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import cfg_get


def rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI：涨跌幅用 alpha=1/period 的指数平滑。"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    both_zero = (avg_loss == 0) & (avg_gain == 0)
    only_gain = (avg_loss == 0) & (avg_gain > 0)
    rsi = rsi.mask(only_gain, 100.0)
    rsi = rsi.mask(both_zero, 50.0)
    return rsi


def add_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """在日线表上追加指标列（基于收盘价）。"""
    out = df.copy()
    close = out["close"].astype(float)

    ma_s = int(cfg_get(cfg, "ma.short", 8))
    ma_l = int(cfg_get(cfg, "ma.long", 24))
    ma_t = int(cfg_get(cfg, "ma.trend", 120))
    out["ma_short"] = close.rolling(ma_s, min_periods=ma_s).mean()
    out["ma_long"] = close.rolling(ma_l, min_periods=ma_l).mean()
    out["ma_trend"] = close.rolling(ma_t, min_periods=ma_t).mean()

    fast = int(cfg_get(cfg, "macd.fast", 12))
    slow = int(cfg_get(cfg, "macd.slow", 26))
    signal = int(cfg_get(cfg, "macd.signal", 9))
    ema_fast = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    out["dif"] = ema_fast - ema_slow
    out["dea"] = out["dif"].ewm(span=signal, adjust=False, min_periods=signal).mean()
    # 国内常用柱状图 = 2 * (DIF - DEA)
    out["hist"] = 2.0 * (out["dif"] - out["dea"])

    boll_n = int(cfg_get(cfg, "bollinger.period", 20))
    boll_k = float(cfg_get(cfg, "bollinger.std_mult", 2.0))
    mid = close.rolling(boll_n, min_periods=boll_n).mean()
    std = close.rolling(boll_n, min_periods=boll_n).std(ddof=0)
    out["boll_mid"] = mid
    out["boll_upper"] = mid + boll_k * std
    out["boll_lower"] = mid - boll_k * std

    out["rsi"] = rsi_wilder(close, int(cfg_get(cfg, "rsi.period", 6)))
    out["rsi2"] = rsi_wilder(close, int(cfg_get(cfg, "rsi.period2", 12)))
    out["rsi3"] = rsi_wilder(close, int(cfg_get(cfg, "rsi.period3", 24)))
    return out
