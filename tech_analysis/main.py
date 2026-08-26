"""技术面分析入口。

用法：
  python tech_analysis/main.py
  python -m tech_analysis
  在本目录执行 python main.py

自选股与配置均以本目录为基准，不依赖当前工作目录，也不读取主项目 config.yaml。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd


def _ensure_project_root_on_path() -> None:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_ensure_project_root_on_path()

from tech_analysis.charts import plot_analysis  # noqa: E402
from tech_analysis.config import cfg_get, load_config, resolve_under_package  # noqa: E402
from tech_analysis.indicators import add_indicators  # noqa: E402
from tech_analysis.market import fetch_daily, remember_name, resolve_name  # noqa: E402
from tech_analysis.network import RateLimiter, disable_proxies, enable_browser_tls  # noqa: E402
from tech_analysis.report import (  # noqa: E402
    StockResult,
    stock_dir_name,
    write_daily_csv,
    write_signals_csv,
    write_stock_report,
    write_summary,
)
from tech_analysis.signals import detect_all  # noqa: E402
from tech_analysis.watchlist import load_watchlist  # noqa: E402

log = logging.getLogger("tech_analysis")


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="自选股技术面分析（独立模块）")
    parser.add_argument("--config", default=None, help="配置文件路径，默认本目录 config.yaml")
    parser.add_argument("--watchlist", default=None, help="自选股 txt/csv 路径，默认配置中的文件")
    parser.add_argument("-v", "--verbose", action="store_true", help="调试日志")
    return parser.parse_args()


def _snapshot(df: pd.DataFrame) -> dict:
    row = df.iloc[-1]
    keys = [
        "close", "ma_short", "ma_long", "ma_trend", "dif", "dea", "hist",
        "boll_mid", "boll_upper", "boll_lower", "rsi", "rsi2", "rsi3",
    ]
    snap = {k: (float(row[k]) if pd.notna(row[k]) else None) for k in keys if k in row.index}
    snap["date"] = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
    return snap


def analyze_one(code: str, name: str, cfg: dict, limiter: RateLimiter, out_root: Path) -> StockResult:
    lookback_cfg = int(cfg_get(cfg, "lookback_trading_days", 120))
    name = resolve_name(code, name, cfg, limiter)
    df = fetch_daily(code, cfg, limiter)
    df = add_indicators(df, cfg)
    used_lookback = min(lookback_cfg, len(df))
    signals = detect_all(df, cfg)
    folder = out_root / stock_dir_name(code, name)
    folder.mkdir(parents=True, exist_ok=True)

    last = df.iloc[-1]
    result = StockResult(
        code=code,
        name=name,
        ok=True,
        last_date=pd.Timestamp(last["date"]).strftime("%Y-%m-%d"),
        last_close=float(last["close"]),
        signals=signals,
        snapshot=_snapshot(df),
        out_dir=folder,
        bars=len(df),
        lookback=used_lookback,
    )
    write_daily_csv(df.iloc[-used_lookback:], folder / "日线指标.csv")
    write_signals_csv(signals, folder / "signals.csv")
    write_stock_report(result, cfg, folder / "分析报告.md")
    title = f"{code} {name} 技术面".strip()
    plot_analysis(df, signals, cfg, title, folder / "技术分析.png")
    log.info("%s %s：回看 %d 日，检出 %d 条信号 → %s", code, name, used_lookback, len(signals), folder)
    return result


def run(cfg: dict, watchlist_path: Path) -> int:
    items = load_watchlist(watchlist_path)
    if not items:
        log.error("自选股清单为空：%s", watchlist_path)
        return 1
    raw_out = Path(cfg_get(cfg, "paths.output_dir", "output"))
    if raw_out.is_absolute():
        out_root = raw_out
    else:
        try:
            from launcher.paths import tech_output_dir

            out_root = tech_output_dir()
        except Exception:
            out_root = resolve_under_package(raw_out)
    out_root.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(float(cfg_get(cfg, "network.rate_limit_seconds", 1.5)))
    log.info("自选股 %d 只，清单 %s，输出 %s", len(items), watchlist_path, out_root)
    for item in items:
        if item.name:
            remember_name(item.code, item.name)

    results: list[StockResult] = []
    for i, item in enumerate(items, 1):
        log.info("[%d/%d] 分析 %s %s", i, len(items), item.code, item.name)
        try:
            results.append(analyze_one(item.code, item.name, cfg, limiter, out_root))
        except Exception as exc:
            log.error("分析失败 %s：%s", item.code, exc)
            log.debug("分析失败详情", exc_info=True)
            results.append(StockResult(code=item.code, name=item.name, ok=False, error=str(exc)))

    write_summary(results, out_root / "信号汇总.md", out_root / "信号汇总.csv")
    ok_n = sum(1 for r in results if r.ok)
    log.info("完成：成功 %d / %d，汇总已写入 %s", ok_n, len(results), out_root)
    return 0 if ok_n else 1


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = parse_args()
    setup_logging(args.verbose)
    cfg = load_config(args.config)
    if not cfg_get(cfg, "network.use_system_proxy", False):
        disable_proxies()
        log.info("已绕过系统代理，直连国内行情源")
    enable_browser_tls()

    if args.watchlist:
        watchlist_path = Path(args.watchlist)
        if not watchlist_path.is_absolute():
            watchlist_path = Path.cwd() / watchlist_path
            if not watchlist_path.exists():
                watchlist_path = resolve_under_package(args.watchlist)
    else:
        try:
            from launcher.paths import ensure_watchlist

            watchlist_path = ensure_watchlist()
        except Exception:
            watchlist_path = resolve_under_package(cfg_get(cfg, "paths.watchlist", "watchlist.txt"))
    return run(cfg, watchlist_path)


if __name__ == "__main__":
    raise SystemExit(main())
