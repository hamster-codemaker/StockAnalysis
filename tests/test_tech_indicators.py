"""技术面指标默认参数与 120 交易日作图（不拉全市场）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_analysis.charts import plot_analysis
from tech_analysis.config import cfg_get, load_config
from tech_analysis.indicators import add_indicators


def _bars(n: int = 250) -> pd.DataFrame:
    close = pd.Series(range(10, 10 + n), dtype=float)
    return pd.DataFrame(
        {
            "date": pd.date_range("2025-01-02", periods=n, freq="B"),
            "open": close - 0.2,
            "high": close + 0.3,
            "low": close - 0.4,
            "close": close,
            "volume": [1_000_000] * n,
        }
    )


class TestIndicatorParams(unittest.TestCase):
    def test_config_defaults_match_brokers(self):
        cfg = load_config()
        self.assertEqual(int(cfg_get(cfg, "lookback_trading_days", 0)), 120)
        self.assertGreaterEqual(int(cfg_get(cfg, "warmup_extra_days", 0)), 130)
        self.assertEqual(int(cfg_get(cfg, "ma.short", 0)), 8)
        self.assertEqual(int(cfg_get(cfg, "ma.long", 0)), 24)
        self.assertEqual(int(cfg_get(cfg, "ma.trend", 0)), 120)
        self.assertEqual(int(cfg_get(cfg, "bollinger.period", 0)), 20)
        self.assertEqual(float(cfg_get(cfg, "bollinger.std_mult", 0)), 2.0)
        self.assertEqual(int(cfg_get(cfg, "macd.fast", 0)), 12)
        self.assertEqual(int(cfg_get(cfg, "macd.slow", 0)), 26)
        self.assertEqual(int(cfg_get(cfg, "macd.signal", 0)), 9)
        self.assertEqual(int(cfg_get(cfg, "rsi.period", 0)), 6)
        self.assertEqual(int(cfg_get(cfg, "rsi.period2", 0)), 12)
        self.assertEqual(int(cfg_get(cfg, "rsi.period3", 0)), 24)
        raw = yaml.safe_load((ROOT / "tech_analysis" / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(raw["rsi"]["period"], 6)

    def test_add_indicators_columns_and_ma120_window(self):
        cfg = load_config()
        df = add_indicators(_bars(250), cfg)
        for col in ("ma_short", "ma_long", "ma_trend", "boll_mid", "dif", "dea", "hist", "rsi", "rsi2", "rsi3"):
            self.assertIn(col, df.columns)
        visible = df.iloc[-120:]
        self.assertFalse(visible["ma_trend"].isna().any())
        self.assertFalse(visible["rsi"].isna().any())
        self.assertGreaterEqual(len(df), 250)

    def test_plot_last_120_trading_days(self):
        cfg = load_config()
        df = add_indicators(_bars(250), cfg)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "技术分析.png"
            plot_analysis(df, [], cfg, "600900 测试", dest)
            self.assertTrue(dest.is_file())
            self.assertGreater(dest.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
