"""东财集群熔断、K 线后备与 get_spot clist（不打全市场、不下载 PDF）。"""

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

from dianjin.em_clist import CLIST_BREAKER, ClistError, SPOT_FIELDS, fetch_clist_page, reset_clist_breaker
from dianjin.em_cluster import ClusterBreaker, is_hard_fail
from dianjin.kline import reset_kline_breaker
from dianjin.source_pref import set_preference_path


CURL56 = RuntimeError("curl: (56) Connection closed abruptly")


class TestClusterBreaker(unittest.TestCase):
    def test_hard_fail_tokens(self):
        self.assertTrue(is_hard_fail(CURL56))
        self.assertTrue(
            is_hard_fail(
                RuntimeError(
                    "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"
                )
            )
        )
        self.assertFalse(is_hard_fail(RuntimeError("HTTP 500")))

    def test_sticky_and_dead_hosts(self):
        b = ClusterBreaker("t", ("https://a", "https://b", "https://c"), trip_after=2, hard_fail_host_cap=2)
        b.record_host_success("https://c")
        self.assertEqual(b.host_order()[0], "https://c")
        b.record_host_failure("https://a", CURL56)
        self.assertNotIn("https://a", b.host_order())
        self.assertIn("https://c", b.host_order())

    def test_trips_after_two_soft_failures(self):
        b = ClusterBreaker("t", ("https://a",), trip_after=2, hard_fail_host_cap=2)
        self.assertFalse(b.record_attempt_failure(all_hard=False))
        self.assertFalse(b.is_open)
        self.assertTrue(b.record_attempt_failure(all_hard=False))
        self.assertTrue(b.is_open)

    def test_all_hard_trips_immediately(self):
        b = ClusterBreaker("t", ("https://a",), trip_after=2, hard_fail_host_cap=2)
        self.assertTrue(b.record_attempt_failure(all_hard=True))
        self.assertTrue(b.is_open)

    def test_announce_once(self):
        b = ClusterBreaker("t", ("https://a",), announce_message="once-only")
        logger = logging.getLogger("test_em_cluster.announce")
        with self.assertLogs(logger, level="INFO") as cm:
            b.announce(logger)
            b.announce(logger)
        self.assertEqual(sum(1 for r in cm.records if "once-only" in r.getMessage()), 1)


class TestKlineCircuit(unittest.TestCase):
    """东财 his 只作为最后手段；主机熔断仍有效。详见 tests/test_source_pref.py。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        set_preference_path(Path(self.tmp.name) / "source_preference.yaml")
        reset_kline_breaker()

    def tearDown(self):
        reset_kline_breaker()
        set_preference_path(None)
        self.tmp.cleanup()

    def test_eastmoney_breaker_still_caps_hosts(self):
        from dianjin.kline import KLINE_BREAKER, fetch_eastmoney_bars

        calls: list[str] = []

        def boom(url, params, timeout):
            calls.append(url)
            raise CURL56

        with patch("dianjin.kline._impersonated_get", boom):
            with self.assertRaises(Exception):
                fetch_eastmoney_bars("603856")
            n1 = len(calls)
            with self.assertRaises(Exception):
                fetch_eastmoney_bars("603811")
            n2 = len(calls)
        self.assertLessEqual(n1, 2)
        self.assertEqual(n2, n1)
        self.assertTrue(KLINE_BREAKER.is_open)


class TestClistCircuit(unittest.TestCase):
    def setUp(self):
        reset_clist_breaker()

    def tearDown(self):
        reset_clist_breaker()

    def test_page_hard_fail_caps_hosts_and_trips(self):
        calls: list[str] = []

        def boom(url, params, timeout, headers):
            calls.append(url)
            raise CURL56

        with patch("dianjin.em_clist.impersonated_get", boom):
            with self.assertRaises(ClistError):
                fetch_clist_page(1, 100, SPOT_FIELDS)
            n1 = len(calls)
            with self.assertRaises(ClistError):
                fetch_clist_page(2, 100, SPOT_FIELDS)
            n2 = len(calls)

        self.assertLessEqual(n1, 2)
        self.assertEqual(n2, n1)
        self.assertTrue(CLIST_BREAKER.is_open)

    def test_sticky_host_skips_dead_numbered_push2(self):
        class Resp:
            status_code = 200

            def json(self):
                return {"data": {"diff": [{"f12": "600900", "f2": 1}], "total": 1}}

        def get(url, params, timeout, headers):
            if "push2delay" not in url:
                raise CURL56
            return Resp()

        with patch("dianjin.em_clist.impersonated_get", get):
            rows, total = fetch_clist_page(1, 100, SPOT_FIELDS)
            self.assertEqual(total, 1)
            self.assertEqual(rows[0]["f12"], "600900")
            self.assertIn("push2delay", CLIST_BREAKER.last_good or "")
            fetch_clist_page(2, 100, SPOT_FIELDS)
            self.assertIn("push2delay", CLIST_BREAKER.last_good or "")


class TestGetSpot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        set_preference_path(Path(self.tmp.name) / "source_preference.yaml")
        reset_clist_breaker()
        from stock_screener.datasources.market import reset_spot_breaker

        reset_spot_breaker()

    def tearDown(self):
        from stock_screener.datasources.market import reset_spot_breaker

        reset_spot_breaker()
        reset_clist_breaker()
        set_preference_path(None)
        self.tmp.cleanup()

    def test_clist_retired_not_used(self):
        from stock_screener.datasources import market

        with (
            patch.object(market, "_spot_from_tencent", side_effect=RuntimeError("tx")),
            patch.object(market, "_spot_from_datacenter", side_effect=RuntimeError("dc")),
            patch.object(market, "_spot_from_clist") as clist,
        ):
            df = market.get_spot()
        self.assertIsNone(df)
        clist.assert_not_called()


if __name__ == "__main__":
    unittest.main()
