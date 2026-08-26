"""股息率解析：单位、错字段、中油资本 1.50 口径。"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dianjin.rules import classify_lists, passes_dividend
from dianjin.snapshot import _normalize_row
from dianjin.ths_yield import ths_ttm_yield_percent
from dianjin.yield_quote import (
    TX_PE_DYN_INDEX,
    TX_PE_STATIC_INDEX,
    TX_PE_TTM_INDEX,
    amplitude_from_tencent_fields,
    amplitude_from_ths_items,
    dps_from_10_share_payout,
    parse_realhead_items,
    parse_tencent_quotes,
    parse_yield_percent,
    pe_dyn_from_tencent_fields,
    screen_yield_from_clist,
    ttm_yield_percent,
    yield_from_dps,
    yield_from_tencent_fields,
)

FIXTURE = ROOT / "tests" / "fixtures" / "yield_000617.json"
FIXTURE_SW = ROOT / "tests" / "fixtures" / "yield_603508.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestYieldUnits(unittest.TestCase):
    def test_already_percent_not_halved_or_scaled(self):
        self.assertAlmostEqual(parse_yield_percent(1.50), 1.50)
        self.assertAlmostEqual(parse_yield_percent("1.50"), 1.50)
        self.assertAlmostEqual(parse_yield_percent("3.03"), 3.03)
        self.assertIsNone(parse_yield_percent("-"))
        self.assertIsNone(parse_yield_percent(None))

    def test_ratio_0_015_stays_tiny_not_auto_times_100(self):
        self.assertAlmostEqual(parse_yield_percent(0.015), 0.015)
        self.assertFalse(passes_dividend(0.015, 3.0))
        self.assertTrue(passes_dividend(1.50, 1.0))

    def test_ten_share_payout_must_divide_by_ten(self):
        self.assertAlmostEqual(dps_from_10_share_payout(0.47), 0.047)
        price = 6.79
        wrong = yield_from_dps(0.47, price)
        right = yield_from_dps(0.047, price)
        self.assertGreater(wrong, 6.0)
        self.assertAlmostEqual(right, 0.047 / 6.79 * 100, places=4)


class TestWrongFields(unittest.TestCase):
    def test_clist_uses_f133_never_f183(self):
        data = _fixture()
        raw = data["em_clist"]
        self.assertAlmostEqual(screen_yield_from_clist(raw), 1.5)
        self.assertAlmostEqual(raw["f183"], 3.03)
        self.assertNotAlmostEqual(screen_yield_from_clist(raw), raw["f183"])
        self.assertIsNone(screen_yield_from_clist({"f183": 3.03, "f12": "000617"}))

    def test_snapshot_normalize_reads_f133(self):
        row = _normalize_row(_fixture()["em_clist"])
        self.assertIsNotNone(row)
        self.assertEqual(row["code"], "000617")
        self.assertAlmostEqual(row["dividend"], 1.5)
        self.assertNotAlmostEqual(row["dividend"], 3.03)

    def test_ths_526792_is_amplitude_not_yield(self):
        items = parse_realhead_items(_fixture()["ths_realhead"])
        self.assertAlmostEqual(amplitude_from_ths_items(items), 2.316)
        self.assertAlmostEqual(screen_yield_from_clist(_fixture()["em_clist"]), 1.5)
        self.assertGreater(abs(2.316 - 1.50), 0.5)

    def test_f183_would_falsely_pass_3pct(self):
        self.assertTrue(passes_dividend(3.03, 3.0))
        self.assertFalse(passes_dividend(1.50, 3.0))
        main, extra = classify_lists(
            dividend=1.50,
            pe_dyn=13.6,
            pe_static=19.9,
            pe_ttm=18.7,
            close=6.0,
            ma120=10.0,
        )
        self.assertFalse(main)
        self.assertFalse(extra)


class TestTencentAndCompute(unittest.TestCase):
    def test_tencent_field_64_is_1_50_field_43_is_amplitude(self):
        quotes = parse_tencent_quotes(_fixture()["tencent"])
        fields = quotes["000617"]
        self.assertAlmostEqual(yield_from_tencent_fields(fields), 1.50)
        self.assertAlmostEqual(amplitude_from_tencent_fields(fields), 2.32)

    def test_tencent_index_52_is_dyn_pe_not_ttm(self):
        quotes = parse_tencent_quotes(_fixture()["tencent"])
        fields = quotes["000617"]
        self.assertEqual(TX_PE_TTM_INDEX, 39)
        self.assertEqual(TX_PE_DYN_INDEX, 52)
        self.assertEqual(TX_PE_STATIC_INDEX, 53)
        self.assertAlmostEqual(float(fields[39]), 18.69)
        self.assertAlmostEqual(float(fields[52]), 13.64)
        self.assertAlmostEqual(float(fields[53]), 19.96)
        self.assertAlmostEqual(pe_dyn_from_tencent_fields(fields), 13.64)
        self.assertNotAlmostEqual(pe_dyn_from_tencent_fields(fields), float(fields[39]))

    def test_ttm_from_dividends_matches_1_50(self):
        data = _fixture()
        as_of = date.fromisoformat(data["as_of"])
        got = ttm_yield_percent(data["dividends"], data["close"], as_of=as_of)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got, 1.50, places=2)
        too_wide = ttm_yield_percent(data["dividends"], data["close"], as_of=as_of, window_days=500)
        self.assertGreater(too_wide, 2.2)

    def test_compare_summary_includes_cnpc(self):
        from tools.compare_yields import attach_ths, rows_from_clist, summarize

        data = _fixture()
        rows = rows_from_clist([data["em_clist"]])
        attach_ths(rows, {"000617": 1.50})
        stats = summarize(rows)
        self.assertEqual(stats["n"], 1)
        self.assertEqual(stats["cnpc"]["code"], "000617")
        self.assertAlmostEqual(stats["cnpc"]["ours"], 1.5)
        self.assertAlmostEqual(stats["cnpc"]["official"], 1.50)
        self.assertLess(stats["cnpc"]["abs_diff"], 0.05)
        self.assertAlmostEqual(stats["pct_within_0_05"], 100.0)


class TestThsTtmAnchors(unittest.TestCase):
    def test_cnpc_ths_ttm_is_1_50(self):
        data = _fixture()
        as_of = date.fromisoformat(data["as_of"])
        payouts = [
            {
                "status": row["status"],
                "ex_date": row["ex_date"],
                "pretax_bonus_rmb": row["pretax_bonus_rmb"],
                "payout_ratio": 15.0,
            }
            for row in data["dividends"]
        ]
        got = ths_ttm_yield_percent(payouts, data["close"], as_of=as_of)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got, 1.50, places=2)

    def test_thinker_ths_ttm_excludes_special_21(self):
        data = json.loads(FIXTURE_SW.read_text(encoding="utf-8"))
        as_of = date.fromisoformat(data["as_of"])
        got = ths_ttm_yield_percent(data["payouts"], data["close"], as_of=as_of)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got, 2.43, places=2)
        naive = ttm_yield_percent(
            [
                {
                    "status": "实施分配",
                    "ex_date": row["ex_date"],
                    "pretax_bonus_rmb": row["pretax_bonus_rmb"],
                }
                for row in data["payouts"]
            ],
            data["close"],
            as_of=as_of,
        )
        self.assertGreater(naive, 12.0)


class TestPriceAdjustedYield(unittest.TestCase):
    def test_yield_tracks_price_with_fixed_dps(self):
        data = _fixture()
        as_of = date.fromisoformat(data["as_of"])
        price = float(data["close"])
        listed = ths_ttm_yield_percent(
            [
                {
                    "status": row["status"],
                    "ex_date": row["ex_date"],
                    "pretax_bonus_rmb": row["pretax_bonus_rmb"],
                    "payout_ratio": 15.0,
                }
                for row in data["dividends"]
            ],
            price,
            as_of=as_of,
        )
        self.assertIsNotNone(listed)
        dps = listed / 100.0 * price
        for factor in (0.9, 1.0, 1.1, 1.25):
            new_price = price * factor
            got = yield_from_dps(dps, new_price)
            self.assertAlmostEqual(got, listed / factor, places=6)


if __name__ == "__main__":
    unittest.main()
