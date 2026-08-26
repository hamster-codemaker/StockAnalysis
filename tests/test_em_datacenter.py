"""数据中心估值行映射：字段对齐原 clist 归一化结构，不改筛选规则。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dianjin.em_datacenter import normalize_datacenter_row, overlay_pe_dyn
from dianjin.rules import classify_lists
from dianjin.snapshot import _normalize_row


class TestDatacenterNormalize(unittest.TestCase):
    def test_maps_lyr_ttm_dv_and_leaves_dyn_for_overlay(self):
        row = normalize_datacenter_row(
            {
                "SECURITY_CODE": "000617",
                "SECURITY_NAME_ABBR": "中油资本",
                "CLOSE_PRICE": 6.79,
                "PE_LAR": 19.9,
                "PE_TTM": 18.7,
                "DV_TTM": 1.50,
            }
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["code"], "000617")
        self.assertEqual(row["name"], "中油资本")
        self.assertAlmostEqual(row["close"], 6.79)
        self.assertIsNone(row["pe_dyn"])
        self.assertAlmostEqual(row["pe_static"], 19.9)
        self.assertAlmostEqual(row["pe_ttm"], 18.7)
        self.assertAlmostEqual(row["dividend"], 1.50)

    def test_pe_same_as_ttm_not_used_as_dyn(self):
        row = normalize_datacenter_row(
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "CLOSE_PRICE": 10.0,
                "PE": 5.0,
                "PE_TTM": 5.0,
                "PE_LAR": 6.0,
                "DV_TTM": 4.0,
            }
        )
        self.assertIsNone(row["pe_dyn"])

    def test_overlay_pe_dyn_fills_only_missing(self):
        rows = [
            {"code": "000617", "pe_dyn": None},
            {"code": "600900", "pe_dyn": 13.0},
        ]
        filled = overlay_pe_dyn(rows, {"000617": 13.6, "600900": 99.0})
        self.assertEqual(filled, 1)
        self.assertAlmostEqual(rows[0]["pe_dyn"], 13.6)
        self.assertAlmostEqual(rows[1]["pe_dyn"], 13.0)

    def test_002318_new_half_year_dyn_pe_excludes_after_report(self):
        row = normalize_datacenter_row(
            {
                "SECURITY_CODE": "002318",
                "SECURITY_NAME_ABBR": "久立特材",
                "CLOSE_PRICE": 19.58,
                "PE_LAR": 12.67612678,
                "PE_TTM": 17.80678842,
            }
        )
        self.assertIsNotNone(row)
        self.assertIsNone(row["pe_dyn"])
        self.assertEqual(overlay_pe_dyn([row], {"002318": 24.55}), 1)
        main, extra = classify_lists(
            dividend=5.1072523,
            pe_dyn=row["pe_dyn"],
            pe_static=row["pe_static"],
            pe_ttm=row["pe_ttm"],
            close=19.58,
            ma120=24.985425,
        )
        self.assertFalse(main)
        self.assertFalse(extra)

    def test_clist_fixture_still_normalizes(self):
        raw = {
            "f12": "000617",
            "f14": "中油资本",
            "f2": 6.79,
            "f9": 13.6,
            "f114": 19.9,
            "f115": 18.7,
            "f133": 1.50,
            "f183": 3.03,
        }
        row = _normalize_row(raw)
        self.assertAlmostEqual(row["dividend"], 1.50)
        self.assertAlmostEqual(row["pe_dyn"], 13.6)


if __name__ == "__main__":
    unittest.main()
