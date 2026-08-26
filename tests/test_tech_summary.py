"""点金术总表技术指标：复用日线快照，不另拉 K 线。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dianjin.report import extra_dir, main_dir, write_reports
from dianjin.screen import ScreenResult
from dianjin.tech_summary import (
    TECH_COLUMNS,
    boll_position,
    cells_from_daily_csv,
    format_tech_cells,
    last_two_indicator_rows,
    macd_state,
)
from tests.test_dianjin import _hit


class TestTechSummaryFormat(unittest.TestCase):
    def test_boll_and_macd_labels(self):
        self.assertTrue(boll_position(11.0, 10.0, 8.0).startswith("上轨上"))
        self.assertTrue(boll_position(7.0, 10.0, 8.0).startswith("下轨下"))
        self.assertTrue(boll_position(9.0, 10.0, 8.0).startswith("中轨"))
        self.assertEqual(macd_state(0.2, 0.1, 0.1, 0.05, 0.12), "金叉")
        self.assertEqual(macd_state(0.05, 0.12, -0.1, 0.2, 0.1), "死叉")
        self.assertEqual(macd_state(0.2, 0.1, 0.1), "多头")
        self.assertEqual(macd_state(-0.2, -0.1, -0.1), "空头")
        self.assertEqual(macd_state(None, 0.1), "—")

    def test_format_cells_and_csv_fallback(self):
        snap = {
            "date": "2026-08-18",
            "close": 9.0,
            "ma_short": 8.8,
            "ma_long": 9.2,
            "dif": 0.12,
            "dea": 0.10,
            "hist": 0.04,
            "boll_upper": 10.0,
            "boll_mid": 9.0,
            "boll_lower": 8.0,
            "rsi": 55.4,
            "rsi2": 48.1,
            "rsi3": 44.0,
        }
        cells = format_tech_cells(snap, {"dif": 0.08, "dea": 0.11})
        self.assertEqual(cells["指标日"], "2026-08-18")
        self.assertEqual(cells["MA8"], "8.80")
        self.assertEqual(cells["MA24"], "9.20")
        self.assertTrue(cells["BOLL"].startswith("中轨"))
        self.assertEqual(cells["MACD"], "金叉")
        self.assertEqual(cells["RSI6"], "55.4")
        self.assertEqual(cells["RSI12"], "48.1")
        self.assertEqual(cells["RSI24"], "44.0")
        self.assertEqual(format_tech_cells(None)["MA8"], "—")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "日线指标.csv"
            path.write_text(
                "date,close,ma_short,ma_long,ma_trend,dif,dea,hist,"
                "boll_mid,boll_upper,boll_lower,rsi,rsi2,rsi3\n"
                "2026-08-17,8.9,8.7,9.1,10.0,0.08,0.11,-0.06,9.0,10.0,8.0,40,42,43\n"
                "2026-08-18,9.0,8.8,9.2,10.1,0.12,0.10,0.04,9.0,10.0,8.0,55.4,48.1,44.0\n",
                encoding="utf-8-sig",
            )
            last, prev = last_two_indicator_rows(path)
            self.assertAlmostEqual(last["close"], 9.0)
            self.assertAlmostEqual(prev["dif"], 0.08)
            from_csv = cells_from_daily_csv(path)
            self.assertEqual(from_csv["指标日"], "2026-08-18")
            self.assertEqual(from_csv["MACD"], "金叉")


class TestOverviewHasTechColumns(unittest.TestCase):
    def test_main_and_extra_tables(self):
        hit = _hit("600900", "长江电力", extra=True)
        info = {
            "hit": hit,
            "folder": "600900_长江电力",
            "recent_signals": "2026-08-18 MA金叉",
            "in_watchlist": False,
            "tech_cells": {
                "指标日": "2026-08-18",
                "MA8": "8.80",
                "MA24": "9.20",
                "BOLL": "中轨 0.50",
                "MACD": "金叉",
                "RSI6": "55.4",
                "RSI12": "48.1",
                "RSI24": "44.0",
            },
        }
        screen = ScreenResult(hits=[hit], extra=[hit], snapshot_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_reports(
                screen,
                out,
                stamp="20260818",
                enrich_info=[info],
                extra_enrich_info=[info],
                screen_only=False,
                watchlist_codes=set(),
            )
            for folder, stem in ((main_dir(out), "点金术"), (extra_dir(out), "点金术extra")):
                csv = (folder / f"{stem}.csv").read_text(encoding="utf-8-sig")
                md = (folder / "总表.md").read_text(encoding="utf-8")
                for col in TECH_COLUMNS:
                    self.assertIn(col, csv)
                    self.assertIn(col, md)
                self.assertIn("2026-08-18", csv)
                self.assertIn("金叉", csv)
                self.assertIn("55.4", csv)
                self.assertIn("中轨 0.50", md)


if __name__ == "__main__":
    unittest.main()
