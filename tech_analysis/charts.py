"""技术面图表：K 线 + 均线、布林、MACD、RSI（微软雅黑）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .config import cfg_get
from .signals import (
    BOLL_DOWN,
    BOLL_UP,
    MA_DEATH,
    MA_GOLDEN,
    MACD_BOT_DIV,
    MACD_DEATH,
    MACD_GOLDEN,
    MACD_TOP_DIV,
    Signal,
)


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
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "微软雅黑", "SimHei", "SimSun", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _candles(ax, x: np.ndarray, df: pd.DataFrame) -> None:
    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    up = closes >= opens
    colors = np.where(up, "#c0392b", "#1e8449")
    ax.vlines(x, lows, highs, color=colors, linewidth=0.8, zorder=2)
    bodies = np.abs(closes - opens)
    bodies = np.where(bodies < 1e-8, (highs - lows) * 0.02 + 1e-4, bodies)
    bottoms = np.minimum(opens, closes)
    ax.bar(x, bodies, bottom=bottoms, width=0.65, color=colors, linewidth=0, zorder=3)


def _mark(ax, x_map: dict, signals: list[Signal], wanted: set[str], y_of, **kwargs) -> None:
    xs, ys = [], []
    for sig in signals:
        key = pd.Timestamp(sig.date).normalize()
        if sig.signal_type in wanted and key in x_map:
            xs.append(x_map[key])
            ys.append(y_of(sig))
    if xs:
        ax.scatter(xs, ys, zorder=5, **kwargs)


def plot_analysis(
    df: pd.DataFrame,
    signals: list[Signal],
    cfg: dict,
    title: str,
    dest: Path,
) -> Path:
    plt = _setup_matplotlib()
    lookback = int(cfg_get(cfg, "lookback_trading_days", 120))
    plot_df = df.iloc[-lookback:].copy().reset_index(drop=True)
    if plot_df.empty:
        raise ValueError("无数据可绘图")

    ma_s = int(cfg_get(cfg, "ma.short", 8))
    ma_l = int(cfg_get(cfg, "ma.long", 24))
    ma_t = int(cfg_get(cfg, "ma.trend", 120))
    rsi_n = int(cfg_get(cfg, "rsi.period", 6))
    rsi_n2 = int(cfg_get(cfg, "rsi.period2", 12))
    rsi_n3 = int(cfg_get(cfg, "rsi.period3", 24))
    rsi_hi = float(cfg_get(cfg, "rsi.overbought", 80))
    rsi_lo = float(cfg_get(cfg, "rsi.oversold", 20))
    div_win = int(cfg_get(cfg, "macd.divergence_window", 60))

    x = np.arange(len(plot_df))
    x_map = {pd.Timestamp(d).normalize(): i for i, d in enumerate(plot_df["date"])}

    fig, axes = plt.subplots(
        4,
        1,
        figsize=(16, 14),
        sharex=True,
        gridspec_kw={"height_ratios": [2.4, 1.3, 1.2, 1.0]},
    )
    ax_price, ax_boll, ax_macd, ax_rsi = axes

    _candles(ax_price, x, plot_df)
    ax_price.plot(x, plot_df["ma_short"], color="#e67e22", linewidth=1.2, label=f"MA{ma_s}")
    ax_price.plot(x, plot_df["ma_long"], color="#2980b9", linewidth=1.2, label=f"MA{ma_l}")
    if "ma_trend" in plot_df.columns:
        ax_price.plot(x, plot_df["ma_trend"], color="#8e44ad", linewidth=1.3, label=f"MA{ma_t}")
    _mark(
        ax_price, x_map, signals, {MA_GOLDEN},
        lambda s: s.close, marker="^", s=42, color="#27ae60", label="MA金叉",
    )
    _mark(
        ax_price, x_map, signals, {MA_DEATH},
        lambda s: s.close, marker="v", s=42, color="#8e44ad", label="MA死叉",
    )
    ax_price.set_title(title, fontsize=14, pad=10)
    ax_price.set_ylabel("价格")
    ax_price.grid(True, linestyle="--", alpha=0.35)
    ax_price.legend(loc="upper left", fontsize=8, ncol=5)

    ax_boll.plot(x, plot_df["close"], color="#2c3e50", linewidth=1.1, label="收盘")
    ax_boll.plot(x, plot_df["boll_mid"], color="#7f8c8d", linewidth=1.0, label="布林中轨")
    ax_boll.plot(x, plot_df["boll_upper"], color="#c0392b", linewidth=1.0, label="上轨")
    ax_boll.plot(x, plot_df["boll_lower"], color="#1e8449", linewidth=1.0, label="下轨")
    ax_boll.fill_between(x, plot_df["boll_lower"], plot_df["boll_upper"], color="#85c1e9", alpha=0.18)
    _mark(
        ax_boll, x_map, signals, {BOLL_UP},
        lambda s: s.close, marker="o", s=36, color="#c0392b", label="上破",
    )
    _mark(
        ax_boll, x_map, signals, {BOLL_DOWN},
        lambda s: s.close, marker="o", s=36, color="#1e8449", label="下穿",
    )
    ax_boll.set_ylabel("布林")
    ax_boll.grid(True, linestyle="--", alpha=0.35)
    ax_boll.legend(loc="upper left", fontsize=8, ncol=5)

    hist = plot_df["hist"].to_numpy(dtype=float)
    hist_colors = np.where(hist >= 0, "#c0392b", "#1e8449")
    ax_macd.bar(x, hist, color=hist_colors, width=0.7, linewidth=0, label="HIST×2")
    ax_macd.plot(x, plot_df["dif"], color="#2980b9", linewidth=1.1, label="DIF")
    ax_macd.plot(x, plot_df["dea"], color="#e67e22", linewidth=1.1, label="DEA")
    ax_macd.axhline(0, color="#7f8c8d", linewidth=0.8)
    _mark(
        ax_macd, x_map, signals, {MACD_GOLDEN},
        lambda s: s.metrics.get("DIF", 0.0), marker="^", s=36, color="#27ae60", label="MACD金叉",
    )
    _mark(
        ax_macd, x_map, signals, {MACD_DEATH},
        lambda s: s.metrics.get("DIF", 0.0), marker="v", s=36, color="#8e44ad", label="MACD死叉",
    )
    _mark(
        ax_macd, x_map, signals, {MACD_TOP_DIV},
        lambda s: s.metrics.get("DIF", 0.0), marker="D", s=32, color="#c0392b", label="顶背离",
    )
    _mark(
        ax_macd, x_map, signals, {MACD_BOT_DIV},
        lambda s: s.metrics.get("DIF", 0.0), marker="D", s=32, color="#1e8449", label="底背离",
    )
    ax_macd.set_ylabel(f"MACD（背离窗{div_win}日）")
    ax_macd.grid(True, linestyle="--", alpha=0.35)
    ax_macd.legend(loc="upper left", fontsize=8, ncol=4)

    ax_rsi.plot(x, plot_df["rsi"], color="#8e44ad", linewidth=1.2, label=f"RSI{rsi_n}")
    if "rsi2" in plot_df.columns:
        ax_rsi.plot(x, plot_df["rsi2"], color="#2980b9", linewidth=1.0, label=f"RSI{rsi_n2}")
    if "rsi3" in plot_df.columns:
        ax_rsi.plot(x, plot_df["rsi3"], color="#16a085", linewidth=1.0, label=f"RSI{rsi_n3}")
    ax_rsi.axhline(rsi_hi, color="#c0392b", linestyle="--", linewidth=0.9, label=f"超买{rsi_hi:g}")
    ax_rsi.axhline(rsi_lo, color="#1e8449", linestyle="--", linewidth=0.9, label=f"超卖{rsi_lo:g}")
    ax_rsi.axhline(50, color="#95a5a6", linewidth=0.6)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel("RSI（通达信 6/12/24）")
    ax_rsi.grid(True, linestyle="--", alpha=0.35)
    ax_rsi.legend(loc="upper left", fontsize=8, ncol=5)

    step = max(1, len(plot_df) // 10)
    ticks = list(range(0, len(plot_df), step))
    if ticks[-1] != len(plot_df) - 1:
        ticks.append(len(plot_df) - 1)
    labels = [pd.Timestamp(plot_df.loc[i, "date"]).strftime("%Y-%m-%d") for i in ticks]
    ax_rsi.set_xticks(ticks)
    ax_rsi.set_xticklabels(labels, rotation=30, ha="right")

    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(dest, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return dest
