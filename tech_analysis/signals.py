"""按明确规则识别金叉/死叉、MACD 背离、布林破轨、RSI 极值。"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import cfg_get

MA_GOLDEN = "MA金叉"
MA_DEATH = "MA死叉"
MACD_GOLDEN = "MACD金叉"
MACD_DEATH = "MACD死叉"
MACD_TOP_DIV = "MACD顶背离"
MACD_BOT_DIV = "MACD底背离"
BOLL_UP = "布林上破"
BOLL_DOWN = "布林下穿"
RSI_OB = "RSI严重超买"
RSI_OS = "RSI严重超卖"
RSI_LEAVE_OB = "RSI离开超买"
RSI_LEAVE_OS = "RSI离开超卖"

MA_TYPES = {MA_GOLDEN, MA_DEATH}
MACD_CROSS_TYPES = {MACD_GOLDEN, MACD_DEATH}
MACD_DIV_TYPES = {MACD_TOP_DIV, MACD_BOT_DIV}
BOLL_TYPES = {BOLL_UP, BOLL_DOWN}
RSI_TYPES = {RSI_OB, RSI_OS, RSI_LEAVE_OB, RSI_LEAVE_OS}


@dataclass
class Signal:
    date: pd.Timestamp
    signal_type: str
    detail: str
    close: float
    metrics: dict[str, float] = field(default_factory=dict)


def _finite(*values: float) -> bool:
    return all(v is not None and np.isfinite(v) for v in values)


def _date_str(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _analysis_slice(df: pd.DataFrame, lookback: int) -> pd.DataFrame:
    if lookback <= 0 or len(df) <= lookback:
        return df
    return df.iloc[-lookback:].copy()


def detect_ma_crosses(df: pd.DataFrame, lookback: int, short_n: int, long_n: int) -> list[Signal]:
    """金叉：当日短>长 且 前一日短<=长。死叉相反。"""
    work = _analysis_slice(df, lookback)
    signals: list[Signal] = []
    if len(work) < 2:
        return signals
    short = work["ma_short"]
    long = work["ma_long"]
    prev_s, prev_l = short.shift(1), long.shift(1)
    golden = (short > long) & (prev_s <= prev_l)
    death = (short < long) & (prev_s >= prev_l)
    for idx in work.index[golden.fillna(False)]:
        row = work.loc[idx]
        if not _finite(row["ma_short"], row["ma_long"], row["close"]):
            continue
        signals.append(
            Signal(
                date=row["date"],
                signal_type=MA_GOLDEN,
                detail=f"MA{short_n}上穿MA{long_n}（{row['ma_short']:.3f} > {row['ma_long']:.3f}）",
                close=float(row["close"]),
                metrics={"MA短": float(row["ma_short"]), "MA长": float(row["ma_long"])},
            )
        )
    for idx in work.index[death.fillna(False)]:
        row = work.loc[idx]
        if not _finite(row["ma_short"], row["ma_long"], row["close"]):
            continue
        signals.append(
            Signal(
                date=row["date"],
                signal_type=MA_DEATH,
                detail=f"MA{short_n}下穿MA{long_n}（{row['ma_short']:.3f} < {row['ma_long']:.3f}）",
                close=float(row["close"]),
                metrics={"MA短": float(row["ma_short"]), "MA长": float(row["ma_long"])},
            )
        )
    return signals


def detect_macd_crosses(df: pd.DataFrame, lookback: int) -> list[Signal]:
    """MACD 金叉：当日 DIF>DEA 且 前一日 DIF<=DEA。死叉相反。"""
    work = _analysis_slice(df, lookback)
    signals: list[Signal] = []
    if len(work) < 2:
        return signals
    dif, dea = work["dif"], work["dea"]
    prev_d, prev_a = dif.shift(1), dea.shift(1)
    golden = (dif > dea) & (prev_d <= prev_a)
    death = (dif < dea) & (prev_d >= prev_a)
    for idx in work.index[golden.fillna(False)]:
        row = work.loc[idx]
        if not _finite(row["dif"], row["dea"], row["close"]):
            continue
        signals.append(
            Signal(
                date=row["date"],
                signal_type=MACD_GOLDEN,
                detail=f"DIF上穿DEA（{row['dif']:.4f} > {row['dea']:.4f}）",
                close=float(row["close"]),
                metrics={
                    "DIF": float(row["dif"]),
                    "DEA": float(row["dea"]),
                    "HIST": float(row["hist"]),
                },
            )
        )
    for idx in work.index[death.fillna(False)]:
        row = work.loc[idx]
        if not _finite(row["dif"], row["dea"], row["close"]):
            continue
        signals.append(
            Signal(
                date=row["date"],
                signal_type=MACD_DEATH,
                detail=f"DIF下穿DEA（{row['dif']:.4f} < {row['dea']:.4f}）",
                close=float(row["close"]),
                metrics={
                    "DIF": float(row["dif"]),
                    "DEA": float(row["dea"]),
                    "HIST": float(row["hist"]),
                },
            )
        )
    return signals


def detect_macd_divergence(
    df: pd.DataFrame,
    lookback: int,
    window: int,
    min_gap: int,
) -> list[Signal]:
    """MACD 顶/底背离。

    判定窗口为近 ``window`` 个交易日，当日与前高/前低至少相隔 ``min_gap`` 日。

    顶背离：当日收盘达到近 N 日最高（含持平），且这是本轮「处于近 N 日最高」的首日；
    在 [t-N+1, t-min_gap] 内取收盘最高日为前高；价格未低于前高，但 DIF 或 HIST
    低于前高当日（未创新高）。

    底背离：对称（近 N 日最低的首日，DIF 或 HIST 未创新低）。
    """
    close = df["close"].to_numpy(dtype=float)
    dif = df["dif"].to_numpy(dtype=float)
    hist = df["hist"].to_numpy(dtype=float)
    dates = df["date"]
    n = len(df)
    start = max(0, n - lookback)
    signals: list[Signal] = []

    for i in range(max(start, window), n):
        if not _finite(close[i], dif[i], hist[i]):
            continue
        left = i - window + 1
        prev_end = i - min_gap
        if prev_end < left:
            continue
        prior = close[left:i]
        if prior.size == 0 or not np.isfinite(prior).any():
            continue

        yest_left = (i - 1) - window + 1
        yest_ok = yest_left >= 0 and i >= 1

        # ----- 顶背离 -----
        is_n_high = close[i] >= np.nanmax(prior)
        yest_high = False
        if yest_ok and np.isfinite(close[i - 1]):
            yest_prior = close[yest_left:i - 1]
            if yest_prior.size and np.isfinite(yest_prior).any():
                yest_high = close[i - 1] >= np.nanmax(yest_prior)
        if is_n_high and not yest_high:
            seg = close[left : prev_end + 1]
            if np.isfinite(seg).any():
                j = left + int(np.nanargmax(seg))
                if _finite(close[j], dif[j], hist[j]) and close[i] >= close[j]:
                    dif_div = dif[i] < dif[j]
                    hist_div = hist[i] < hist[j]
                    if dif_div or hist_div:
                        bits = [f"近{window}日新高，前高{_date_str(dates.iloc[j])}收盘{close[j]:.3f}"]
                        if dif_div:
                            bits.append(f"DIF未创新高({dif[i]:.4f}<{dif[j]:.4f})")
                        if hist_div:
                            bits.append(f"HIST未创新高({hist[i]:.4f}<{hist[j]:.4f})")
                        signals.append(
                            Signal(
                                date=dates.iloc[i],
                                signal_type=MACD_TOP_DIV,
                                detail="；".join(bits),
                                close=float(close[i]),
                                metrics={
                                    "DIF": float(dif[i]),
                                    "HIST": float(hist[i]),
                                    "前高DIF": float(dif[j]),
                                    "前高HIST": float(hist[j]),
                                },
                            )
                        )

        # ----- 底背离 -----
        is_n_low = close[i] <= np.nanmin(prior)
        yest_low = False
        if yest_ok and np.isfinite(close[i - 1]):
            yest_prior = close[yest_left:i - 1]
            if yest_prior.size and np.isfinite(yest_prior).any():
                yest_low = close[i - 1] <= np.nanmin(yest_prior)
        if is_n_low and not yest_low:
            seg = close[left : prev_end + 1]
            if np.isfinite(seg).any():
                j = left + int(np.nanargmin(seg))
                if _finite(close[j], dif[j], hist[j]) and close[i] <= close[j]:
                    dif_div = dif[i] > dif[j]
                    hist_div = hist[i] > hist[j]
                    if dif_div or hist_div:
                        bits = [f"近{window}日新低，前低{_date_str(dates.iloc[j])}收盘{close[j]:.3f}"]
                        if dif_div:
                            bits.append(f"DIF未创新低({dif[i]:.4f}>{dif[j]:.4f})")
                        if hist_div:
                            bits.append(f"HIST未创新低({hist[i]:.4f}>{hist[j]:.4f})")
                        signals.append(
                            Signal(
                                date=dates.iloc[i],
                                signal_type=MACD_BOT_DIV,
                                detail="；".join(bits),
                                close=float(close[i]),
                                metrics={
                                    "DIF": float(dif[i]),
                                    "HIST": float(hist[i]),
                                    "前低DIF": float(dif[j]),
                                    "前低HIST": float(hist[j]),
                                },
                            )
                        )
    return signals


def detect_bollinger(df: pd.DataFrame, lookback: int) -> list[Signal]:
    """上破：前一日收盘<=前一日上轨 且 当日收盘>当日上轨。下穿对称。"""
    work = _analysis_slice(df, lookback)
    signals: list[Signal] = []
    if len(work) < 2:
        return signals
    close, upper, lower = work["close"], work["boll_upper"], work["boll_lower"]
    up = (close > upper) & (close.shift(1) <= upper.shift(1))
    down = (close < lower) & (close.shift(1) >= lower.shift(1))
    for idx in work.index[up.fillna(False)]:
        row = work.loc[idx]
        if not _finite(row["close"], row["boll_upper"]):
            continue
        signals.append(
            Signal(
                date=row["date"],
                signal_type=BOLL_UP,
                detail=f"收盘{row['close']:.3f}上破上轨{row['boll_upper']:.3f}",
                close=float(row["close"]),
                metrics={
                    "布林上轨": float(row["boll_upper"]),
                    "布林中轨": float(row["boll_mid"]),
                    "布林下轨": float(row["boll_lower"]),
                },
            )
        )
    for idx in work.index[down.fillna(False)]:
        row = work.loc[idx]
        if not _finite(row["close"], row["boll_lower"]):
            continue
        signals.append(
            Signal(
                date=row["date"],
                signal_type=BOLL_DOWN,
                detail=f"收盘{row['close']:.3f}下穿下轨{row['boll_lower']:.3f}",
                close=float(row["close"]),
                metrics={
                    "布林上轨": float(row["boll_upper"]),
                    "布林中轨": float(row["boll_mid"]),
                    "布林下轨": float(row["boll_lower"]),
                },
            )
        )
    return signals


def detect_rsi(df: pd.DataFrame, lookback: int, overbought: float, oversold: float) -> list[Signal]:
    """当日 RSI 越过阈值记为严重超买/超卖，并标注刚进入或持续；同时记录刚离开。"""
    work = _analysis_slice(df, lookback)
    signals: list[Signal] = []
    if work.empty:
        return signals
    rsi = work["rsi"]
    prev = rsi.shift(1)
    in_ob = rsi >= overbought
    in_os = rsi <= oversold
    was_ob = prev >= overbought
    was_os = prev <= oversold

    for idx, row in work.iterrows():
        val = row["rsi"]
        if not _finite(val, row["close"]):
            continue
        metrics = {"RSI": float(val)}
        close = float(row["close"])
        date = row["date"]
        if bool(in_ob.loc[idx]):
            just = (not bool(was_ob.loc[idx])) if pd.notna(prev.loc[idx]) else True
            tag = "刚进入" if just else "持续"
            signals.append(
                Signal(
                    date=date,
                    signal_type=RSI_OB,
                    detail=f"RSI={val:.2f}≥{overbought:g}（{tag}）",
                    close=close,
                    metrics=metrics,
                )
            )
        elif bool(was_ob.loc[idx]) if pd.notna(prev.loc[idx]) else False:
            signals.append(
                Signal(
                    date=date,
                    signal_type=RSI_LEAVE_OB,
                    detail=f"RSI={val:.2f}，自超买区回落（阈值{overbought:g}）",
                    close=close,
                    metrics=metrics,
                )
            )
        if bool(in_os.loc[idx]):
            just = (not bool(was_os.loc[idx])) if pd.notna(prev.loc[idx]) else True
            tag = "刚进入" if just else "持续"
            signals.append(
                Signal(
                    date=date,
                    signal_type=RSI_OS,
                    detail=f"RSI={val:.2f}≤{oversold:g}（{tag}）",
                    close=close,
                    metrics=metrics,
                )
            )
        elif bool(was_os.loc[idx]) if pd.notna(prev.loc[idx]) else False:
            signals.append(
                Signal(
                    date=date,
                    signal_type=RSI_LEAVE_OS,
                    detail=f"RSI={val:.2f}，自超卖区回升（阈值{oversold:g}）",
                    close=close,
                    metrics=metrics,
                )
            )
    return signals


def detect_all(df: pd.DataFrame, cfg: dict) -> list[Signal]:
    lookback = int(cfg_get(cfg, "lookback_trading_days", 120))
    signals: list[Signal] = []
    signals.extend(
        detect_ma_crosses(
            df,
            lookback,
            int(cfg_get(cfg, "ma.short", 8)),
            int(cfg_get(cfg, "ma.long", 24)),
        )
    )
    signals.extend(detect_macd_crosses(df, lookback))
    signals.extend(
        detect_macd_divergence(
            df,
            lookback,
            int(cfg_get(cfg, "macd.divergence_window", 60)),
            int(cfg_get(cfg, "macd.divergence_min_gap", 5)),
        )
    )
    signals.extend(detect_bollinger(df, lookback))
    signals.extend(
        detect_rsi(
            df,
            lookback,
            float(cfg_get(cfg, "rsi.overbought", 80)),
            float(cfg_get(cfg, "rsi.oversold", 20)),
        )
    )
    signals.sort(key=lambda s: (pd.Timestamp(s.date), s.signal_type))
    return signals
