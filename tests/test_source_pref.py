"""源优先级持久化、熔断、二次确认（不打全市场）。"""

from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dianjin.kline import clear_bar_cache, fetch_qfq_closes, reset_kline_breaker
from dianjin.source_pref import (
    DEFAULT_ORDER,
    SourceBreaker,
    promote_backup,
    set_preference_path,
    source_order,
)

CURL56 = RuntimeError("curl: (56) Connection closed abruptly")


class TestSourcePreference(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "source_preference.yaml"
        set_preference_path(self.path)
        reset_kline_breaker()

    def tearDown(self):
        reset_kline_breaker()
        set_preference_path(None)
        self.tmp.cleanup()

    def test_default_tencent_first_no_eastmoney(self):
        self.assertEqual(source_order("kline")[0], "tencent")
        self.assertEqual(source_order("kline"), ["tencent"])
        self.assertEqual(source_order("hist")[0], "tencent")
        self.assertEqual(source_order("spot")[0], "tencent")
        self.assertEqual(source_order("spot"), ["tencent", "datacenter"])
        self.assertNotIn("eastmoney", DEFAULT_ORDER["kline"])
        self.assertNotIn("sina", DEFAULT_ORDER["kline"])
        self.assertNotIn("clist", DEFAULT_ORDER["spot"])

    def test_drops_retired_eastmoney_from_saved_yaml(self):
        self.path.write_text(
            "kline:\n  order: [eastmoney, tencent, sina]\nspot:\n  order: [clist, tencent]\n",
            encoding="utf-8",
        )
        self.assertEqual(source_order("kline"), ["tencent"])
        self.assertEqual(source_order("spot"), ["tencent", "datacenter"])

    def test_promote_persists_and_next_read_sees_it(self):
        self.assertTrue(promote_backup("spot", "tencent", "datacenter"))
        self.assertEqual(source_order("spot")[:2], ["datacenter", "tencent"])
        self.assertTrue(self.path.is_file())
        self.assertIn("datacenter", self.path.read_text(encoding="utf-8"))

    def test_no_promote_when_same_or_unknown(self):
        self.assertFalse(promote_backup("kline", "tencent", "tencent"))
        self.assertFalse(promote_backup("kline", "missing", "tencent"))
        self.assertEqual(source_order("kline")[0], "tencent")

    def test_source_breaker_announces_once(self):
        b = SourceBreaker("kline")
        logger = logging.getLogger("test_source_pref.breaker")
        with self.assertLogs(logger, level="INFO") as cm:
            b.mark_dead("tencent", logger, "腾讯本轮不可达")
            b.mark_dead("tencent", logger, "腾讯本轮不可达")
        self.assertEqual(sum(1 for r in cm.records if "腾讯本轮不可达" in r.getMessage()), 1)
        self.assertTrue(b.is_dead("tencent"))


class TestKlineTencentFirst(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        set_preference_path(Path(self.tmp.name) / "source_preference.yaml")
        reset_kline_breaker()
        clear_bar_cache()

    def tearDown(self):
        reset_kline_breaker()
        set_preference_path(None)
        self.tmp.cleanup()

    def test_tencent_success_skips_eastmoney(self):
        bars = [{"date": "2026-01-01", "open": 1, "close": float(i), "high": 1, "low": 1, "volume": 1} for i in range(130)]
        em_calls = []

        def boom(*args, **kwargs):
            em_calls.append(1)
            raise CURL56

        with (
            patch("dianjin.kline.fetch_tencent_bars", return_value=bars),
            patch("dianjin.kline.fetch_eastmoney_bars", boom),
            patch("dianjin.kline.fetch_sina_bars", boom),
        ):
            closes = fetch_qfq_closes("600900")
            more = fetch_qfq_closes("000001")
        self.assertEqual(len(closes), 130)
        self.assertEqual(len(more), 130)
        self.assertEqual(em_calls, [])

    def test_tencent_fail_does_not_use_sina(self):
        bars = [{"date": "2026-01-01", "open": 1, "close": 10.0, "high": 1, "low": 1, "volume": 1}] * 130
        sina_calls = []

        def sina(*args, **kwargs):
            sina_calls.append(1)
            return bars

        with (
            patch("dianjin.kline.fetch_tencent_bars", side_effect=CURL56),
            patch("dianjin.kline.fetch_sina_bars", sina),
            patch("dianjin.kline.fetch_eastmoney_bars", return_value=bars),
        ):
            closes = fetch_qfq_closes("600900")
        self.assertEqual(closes, [])
        self.assertEqual(sina_calls, [])
        self.assertEqual(source_order("kline"), ["tencent"])

    def test_eastmoney_not_called_when_tencent_fails(self):
        calls: list[str] = []

        def boom(url, params, timeout):
            calls.append(url)
            raise CURL56

        with (
            patch("dianjin.kline.fetch_tencent_bars", side_effect=CURL56),
            patch("dianjin.kline.fetch_sina_bars", side_effect=CURL56),
            patch("dianjin.kline._impersonated_get", boom),
        ):
            first = fetch_qfq_closes("603856")
            second = fetch_qfq_closes("603811")
        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertEqual(calls, [])

    def test_no_swap_if_all_fail(self):
        with (
            patch("dianjin.kline.fetch_tencent_bars", side_effect=CURL56),
            patch("dianjin.kline.fetch_sina_bars", side_effect=CURL56),
            patch("dianjin.kline.fetch_eastmoney_bars", side_effect=CURL56),
        ):
            self.assertEqual(fetch_qfq_closes("600000"), [])
        self.assertEqual(source_order("kline"), ["tencent"])


class TestGetSpotOrder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        set_preference_path(Path(self.tmp.name) / "source_preference.yaml")
        from stock_screener.datasources.market import reset_spot_breaker

        reset_spot_breaker()

    def tearDown(self):
        from stock_screener.datasources.market import reset_spot_breaker

        reset_spot_breaker()
        set_preference_path(None)
        self.tmp.cleanup()

    def test_tencent_used_when_available(self):
        from stock_screener.datasources import market

        import pandas as pd

        tx = pd.DataFrame(
            {
                "代码": [f"{i:06d}" for i in range(1200)],
                "名称": [f"N{i}" for i in range(1200)],
                "最新价": [10.0] * 1200,
                "总市值": [1e9] * 1200,
                "流通市值": [8e8] * 1200,
            }
        )
        with (
            patch.object(market, "_spot_from_tencent", return_value=tx) as tx_fn,
            patch.object(market, "_spot_from_datacenter") as dc,
            patch.object(market, "_spot_from_clist") as clist,
        ):
            df = market.get_spot()
        self.assertIsNotNone(df)
        self.assertGreaterEqual(len(df), 1200)
        tx_fn.assert_called_once()
        dc.assert_not_called()
        clist.assert_not_called()

    def test_fallback_logs_once(self):
        from stock_screener.datasources import market

        import pandas as pd

        fallback = pd.DataFrame(
            {
                "代码": ["600900"],
                "名称": ["长江电力"],
                "最新价": [27.96],
                "总市值": [1e11],
                "流通市值": [1e11],
            }
        )
        with (
            patch.object(market, "_spot_from_tencent", side_effect=RuntimeError("tx down")),
            patch.object(market, "_spot_from_datacenter", return_value=fallback),
            patch.object(market, "_spot_from_clist") as clist,
        ):
            with self.assertLogs(market.log, level="INFO") as cm:
                df = market.get_spot()
        self.assertEqual(df.iloc[0]["代码"], "600900")
        joined = "\n".join(r.getMessage() for r in cm.records)
        self.assertIn("腾讯全A排行本轮不可达", joined)
        clist.assert_not_called()


class TestSecondPass(unittest.TestCase):
    def test_ma_second_pass_fills_then_stops(self):
        from dianjin.screen import screen_market

        rows = [
            {
                "code": "600900",
                "name": "长江电力",
                "close": 8.0,
                "pe_dyn": 10.0,
                "pe_static": 11.0,
                "pe_ttm": 12.0,
                "dividend": 4.0,
            }
        ]
        calls = {"n": 0}

        def fake_ma(code, period=120, bar_limit=160, timeout=12.0):
            calls["n"] += 1
            if calls["n"] == 1:
                return 8.0, None, 10
            return 8.0, 10.0, 130

        with patch("dianjin.screen.fetch_close_and_ma120", fake_ma):
            result = screen_market({"dianjin": {"kline_rate_limit": 0.0}}, snapshot_rows=rows)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(result.ma_second_filled, 1)
        self.assertEqual(result.ma_second_missing, 0)
        self.assertEqual(len(result.hits), 1)

    def test_gappy_snapshot_second_pass(self):
        from dianjin.snapshot import fill_missing_valuation

        rows = [
            {
                "code": "600900",
                "name": "长江电力",
                "close": 27.96,
                "pe_dyn": 10.0,
                "pe_static": None,
                "pe_ttm": 12.0,
                "dividend": 3.5,
            }
        ]
        extra = [
            {
                "code": "600900",
                "name": "长江电力",
                "close": 27.96,
                "pe_dyn": 10.0,
                "pe_static": 11.0,
                "pe_ttm": 12.0,
                "dividend": 3.5,
            }
        ]
        with patch("dianjin.snapshot.fetch_valuation_rows", return_value=extra):
            filled, still = fill_missing_valuation(rows)
        self.assertEqual(filled, 1)
        self.assertEqual(still, 0)
        self.assertEqual(rows[0]["pe_static"], 11.0)


class TestFetchDailyOrder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        set_preference_path(Path(self.tmp.name) / "source_preference.yaml")
        reset_kline_breaker()

    def tearDown(self):
        reset_kline_breaker()
        set_preference_path(None)
        self.tmp.cleanup()

    def test_fetch_daily_uses_tencent(self):
        import pandas as pd

        from tech_analysis.market import fetch_daily
        from tech_analysis.network import RateLimiter

        bars = [
            {
                "date": f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                "open": 1,
                "close": 10.0 + i * 0.01,
                "high": 11,
                "low": 9,
                "volume": 100,
            }
            for i in range(160)
        ]
        with (
            patch("dianjin.kline.fetch_tencent_bars", return_value=bars) as tx,
            patch("dianjin.kline.fetch_eastmoney_bars", side_effect=CURL56) as em,
        ):
            df = fetch_daily("600900", {"lookback_trading_days": 120, "network": {"max_retries": 1}}, RateLimiter(0))
        self.assertGreaterEqual(len(df), 120)
        self.assertIn("close", df.columns)
        tx.assert_called()
        em.assert_not_called()


class TestLiveHistSmoke(unittest.TestCase):
    def test_tencent_one_stock_ma120(self):
        reset_kline_breaker()
        closes = fetch_qfq_closes("600900", limit=160, timeout=15.0)
        self.assertGreaterEqual(len(closes), 120, "腾讯日线应一次给出足够计算 MA120 的根数")


if __name__ == "__main__":
    unittest.main()
