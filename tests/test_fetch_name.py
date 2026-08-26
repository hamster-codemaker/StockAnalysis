"""证券简称：本地优先、内存正/负缓存、腾讯解析、东财只试一次并熔断。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_screener.datasources.cninfo import reset_local_stock_map
from tech_analysis.market import (
    fetch_name,
    parse_sina_hq,
    parse_tencent_quote,
    reset_name_lookup,
    resolve_name,
    set_name_data_root,
)
from tech_analysis.network import RateLimiter

CFG = {"network": {"timeout_seconds": 1, "max_retries": 3}}
CURL56 = RuntimeError("Failed to perform, curl: (56) Connection closed abruptly")
TX_000333 = 'v_sz000333="51~美的集团~000333~64.20~0.50~0.78~"'
SINA_000333 = 'var hq_str_sz000333="美的集团,64.20,64.00,64.50,63.80,123456,..."'


class TestFetchName(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data" / "cache").mkdir(parents=True)
        (self.root / "data" / "docs").mkdir(parents=True)
        reset_name_lookup()
        reset_local_stock_map()
        set_name_data_root(self.root)

    def tearDown(self):
        reset_name_lookup()
        reset_local_stock_map()
        set_name_data_root(None)
        self.tmp.cleanup()

    def test_parse_tencent_quote(self):
        self.assertEqual(parse_tencent_quote(TX_000333, "000333"), "美的集团")
        self.assertEqual(parse_tencent_quote('v_sz000333=""', "000333"), "")
        self.assertEqual(parse_tencent_quote('v_sz000333="1~~"', "000333"), "")
        self.assertEqual(parse_sina_hq(SINA_000333, "000333"), "美的集团")
        self.assertEqual(parse_sina_hq('var hq_str_sz000333="";', "000333"), "")

    def test_cache_hit_skips_network(self):
        calls: list[str] = []

        def http(url, timeout, headers=None):
            calls.append(url)
            return TX_000333

        with (
            patch("tech_analysis.market.retry_call", side_effect=AssertionError("retry_call")),
            patch("tech_analysis.market._fetch_eastmoney_name", side_effect=AssertionError("eastmoney")),
            patch("tech_analysis.market._http_get_text", side_effect=http),
        ):
            first = fetch_name("000333", CFG, RateLimiter(0))
            second = fetch_name("000333", CFG, RateLimiter(0))
            again = resolve_name("000333", "", CFG, RateLimiter(0))
        self.assertEqual(first, "美的集团")
        self.assertEqual(second, "美的集团")
        self.assertEqual(again, "美的集团")
        self.assertEqual(len(calls), 1)
        self.assertIn("qt.gtimg.cn", calls[0])

    def test_negative_cache_skips_network(self):
        http_calls: list[int] = []
        em_calls: list[int] = []

        def http(*args, **kwargs):
            http_calls.append(1)
            raise RuntimeError("quote down")

        def em(*args, **kwargs):
            em_calls.append(1)
            raise RuntimeError("个股资料为空")

        with (
            patch("tech_analysis.market.retry_call", side_effect=AssertionError("retry_call")),
            patch("tech_analysis.market._fetch_eastmoney_name", side_effect=em),
            patch("tech_analysis.market._http_get_text", side_effect=http),
        ):
            first = fetch_name("000333", CFG, RateLimiter(0))
            second = fetch_name("000333", CFG, RateLimiter(0))
        self.assertEqual(first, "")
        self.assertEqual(second, "")
        self.assertEqual(len(http_calls), 1)
        self.assertEqual(em_calls, [])

    def test_local_cninfo_no_network(self):
        cache = self.root / "data" / "cache" / "szse_stock.json"
        cache.write_text(
            json.dumps({"stockList": [{"code": "000333", "zwjc": "美的集团", "orgId": "x"}]}),
            encoding="utf-8",
        )
        with (
            patch("tech_analysis.market.retry_call", side_effect=AssertionError("retry_call")),
            patch("tech_analysis.market._fetch_eastmoney_name", side_effect=AssertionError("eastmoney")),
            patch("tech_analysis.market._http_get_text", side_effect=AssertionError("http")),
        ):
            name = fetch_name("000333", CFG, RateLimiter(0))
        self.assertEqual(name, "美的集团")

    def test_local_docs_strips_watch_suffix(self):
        folder = self.root / "data" / "docs" / "000651_格力电器_自选"
        folder.mkdir(parents=True)
        with (
            patch("tech_analysis.market.retry_call", side_effect=AssertionError("retry_call")),
            patch("tech_analysis.market._fetch_eastmoney_name", side_effect=AssertionError("eastmoney")),
            patch("tech_analysis.market._http_get_text", side_effect=AssertionError("http")),
        ):
            name = fetch_name("000651", CFG, RateLimiter(0))
        self.assertEqual(name, "格力电器")

    def test_hint_skips_network(self):
        with (
            patch("tech_analysis.market.retry_call", side_effect=AssertionError("retry_call")),
            patch("tech_analysis.market._fetch_eastmoney_name", side_effect=AssertionError("eastmoney")),
            patch("tech_analysis.market._http_get_text", side_effect=AssertionError("http")),
        ):
            name = resolve_name("000333", "美的集团", CFG, RateLimiter(0))
            again = fetch_name("000333", CFG, RateLimiter(0))
        self.assertEqual(name, "美的集团")
        self.assertEqual(again, "美的集团")

    def test_no_eastmoney_after_tencent_fail(self):
        em_calls: list[str] = []

        def em(code, timeout):
            em_calls.append(code)
            raise CURL56

        with (
            patch("tech_analysis.market.retry_call", side_effect=AssertionError("retry_call")),
            patch("tech_analysis.market._fetch_tencent_name", return_value=""),
            patch("tech_analysis.market._fetch_sina_name", return_value=""),
            patch("tech_analysis.market._fetch_eastmoney_name", side_effect=em),
        ):
            first = fetch_name("000333", CFG, RateLimiter(0))
            second = fetch_name("000651", CFG, RateLimiter(0))
            third = fetch_name("000651", CFG, RateLimiter(0))
        self.assertEqual(first, "")
        self.assertEqual(second, "")
        self.assertEqual(third, "")
        self.assertEqual(em_calls, [])

    def test_persist_used_after_memory_reset(self):
        def http(url, timeout, headers=None):
            return TX_000333

        with (
            patch("tech_analysis.market.retry_call", side_effect=AssertionError("retry_call")),
            patch("tech_analysis.market._fetch_eastmoney_name", side_effect=AssertionError("eastmoney")),
            patch("tech_analysis.market._http_get_text", side_effect=http),
        ):
            self.assertEqual(fetch_name("000333", CFG, RateLimiter(0)), "美的集团")
        reset_name_lookup()
        set_name_data_root(self.root)
        with (
            patch("tech_analysis.market.retry_call", side_effect=AssertionError("retry_call")),
            patch("tech_analysis.market._fetch_eastmoney_name", side_effect=AssertionError("eastmoney")),
            patch("tech_analysis.market._http_get_text", side_effect=AssertionError("http")),
        ):
            self.assertEqual(fetch_name("000333", CFG, RateLimiter(0)), "美的集团")
        cache = self.root / "data" / "cache" / "stock_names.json"
        self.assertTrue(cache.is_file())
        self.assertIn("美的集团", cache.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
