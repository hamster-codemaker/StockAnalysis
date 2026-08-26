"""点金术技术信号汇总：点金术根目录 + 日报日期根目录；extra 独立文件名。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dianjin.report import extra_dir, main_dir, write_empty_reports, write_reports
from dianjin.screen import ScreenResult
from dianjin.signal_summary import (
    DAILY_MAIN_SUMMARY_CSV,
    DAILY_MAIN_SUMMARY_MD,
    EMPTY_MARK,
    EXTRA_SUMMARY_CSV,
    EXTRA_SUMMARY_MD,
    MAIN_SUMMARY_CSV,
    MAIN_SUMMARY_MD,
    NO_SIGNAL_MARK,
    copy_summaries_to_daily_root,
    write_signal_summary,
)
from launcher.daily_report import _copy_dianjin_tree
from tech_analysis.signals import Signal
from tests.test_dianjin import _hit


def _sig(day: str, kind: str, detail: str = "") -> Signal:
    return Signal(date=pd.Timestamp(day), signal_type=kind, detail=detail or kind, close=8.0)


class TestWriteSignalSummary(unittest.TestCase):
    def test_empty_hits_writes_no_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "点金术"
            md, csv = write_signal_summary(
                [],
                dest,
                title="点金术",
                stamp="20260818",
                md_name=MAIN_SUMMARY_MD,
                csv_name=MAIN_SUMMARY_CSV,
            )
            self.assertTrue(md.is_file())
            self.assertTrue(csv.is_file())
            text = md.read_text(encoding="utf-8")
            self.assertIn(EMPTY_MARK, text)
            self.assertNotIn(NO_SIGNAL_MARK, text)
            frame = pd.read_csv(csv, encoding="utf-8-sig")
            self.assertEqual(len(frame), 0)

    def test_hit_without_signals_says_none_today(self):
        hit = _hit("600000", "浦发银行")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "点金术"
            write_signal_summary(
                [hit],
                dest,
                title="点金术",
                stamp="20260818",
                md_name=MAIN_SUMMARY_MD,
                csv_name=MAIN_SUMMARY_CSV,
                info_map={
                    "600000": {
                        "hit": hit,
                        "folder": "600000_浦发银行",
                        "last_date": "2026-08-18",
                        "signals": [],
                    }
                },
            )
            text = (dest / MAIN_SUMMARY_MD).read_text(encoding="utf-8")
            self.assertIn("600000", text)
            self.assertIn("浦发银行", text)
            self.assertIn(NO_SIGNAL_MARK, text)
            self.assertNotIn(EMPTY_MARK, text)

    def test_lists_detected_signals_not_raw_ma_numbers(self):
        extra = _hit("600900", "长江电力", extra=True)
        main_only = _hit("600000", "浦发银行")
        extra_info = {
            "hit": extra,
            "folder": "600900_长江电力",
            "last_date": "2026-08-18",
            "signals": [
                _sig("2026-08-18", "MA金叉", "MA8上穿MA24"),
                _sig("2026-08-18", "布林下穿", "收盘跌破下轨"),
                _sig("2026-08-10", "MACD死叉", "DIF下穿DEA"),
            ],
            "recent_signals": "2026-08-18 MA金叉；2026-08-18 布林下穿",
        }
        main_info = {
            "hit": main_only,
            "folder": "600000_浦发银行",
            "last_date": "2026-08-18",
            "signals": [],
        }
        screen = ScreenResult(hits=[extra, main_only], extra=[extra], snapshot_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_reports(
                screen,
                out,
                stamp="20260818",
                enrich_info=[extra_info, main_info],
                extra_enrich_info=[extra_info],
                screen_only=False,
                watchlist_codes=set(),
            )
            main_md = (main_dir(out) / MAIN_SUMMARY_MD).read_text(encoding="utf-8")
            extra_md = (extra_dir(out) / EXTRA_SUMMARY_MD).read_text(encoding="utf-8")
            self.assertTrue((main_dir(out) / MAIN_SUMMARY_CSV).is_file())
            self.assertTrue((extra_dir(out) / EXTRA_SUMMARY_CSV).is_file())
            self.assertIn("MA金叉", main_md)
            self.assertIn("布林下穿", main_md)
            self.assertIn("MA8上穿MA24", main_md)
            self.assertNotIn("MA8：8.80", main_md)
            self.assertIn(NO_SIGNAL_MARK, main_md)
            self.assertIn("600000", main_md)
            self.assertIn("600900", main_md)
            self.assertIn("600900", extra_md)
            self.assertNotIn("600000", extra_md)
            self.assertIn("MA金叉", extra_md)
            overview = (main_dir(out) / "总表.md").read_text(encoding="utf-8")
            self.assertIn(MAIN_SUMMARY_MD, overview)
            extra_overview = (extra_dir(out) / "总表.md").read_text(encoding="utf-8")
            self.assertIn(EXTRA_SUMMARY_MD, extra_overview)

    def test_reads_signals_csv_without_detect_all(self):
        hit = _hit("600900", "长江电力")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "点金术"
            stock = dest / "个股" / "600900_长江电力"
            stock.mkdir(parents=True)
            (stock / "signals.csv").write_text(
                "日期,类型,细节,收盘价,相关指标\n"
                "2026-08-18,RSI严重超卖,RSI6<=20,7.5,RSI=18.00\n",
                encoding="utf-8-sig",
            )
            with patch("tech_analysis.signals.detect_all") as detect:
                write_signal_summary(
                    [hit],
                    dest,
                    title="点金术",
                    stamp="20260818",
                    md_name=MAIN_SUMMARY_MD,
                    csv_name=MAIN_SUMMARY_CSV,
                    info_map={"600900": {"hit": hit, "folder": "600900_长江电力", "last_date": "2026-08-18"}},
                )
                detect.assert_not_called()
            text = (dest / MAIN_SUMMARY_MD).read_text(encoding="utf-8")
            self.assertIn("RSI严重超卖", text)

    def test_write_empty_reports_creates_both_summaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_empty_reports(out, stamp="20260818")
            self.assertIn(EMPTY_MARK, (main_dir(out) / MAIN_SUMMARY_MD).read_text(encoding="utf-8"))
            self.assertIn(EMPTY_MARK, (extra_dir(out) / EXTRA_SUMMARY_MD).read_text(encoding="utf-8"))


class TestDailyCopyPath(unittest.TestCase):
    def test_copy_lifts_summaries_to_date_root_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "dj_src"
            report = Path(tmp) / "20260818"
            report.mkdir()
            main = src / "点金术"
            extra = src / "点金术extra"
            main.mkdir(parents=True)
            extra.mkdir(parents=True)
            (main / "总表.md").write_text("点金术总表\n", encoding="utf-8")
            (main / MAIN_SUMMARY_MD).write_text("MAIN-SIG 600900 MA金叉\n", encoding="utf-8")
            (main / MAIN_SUMMARY_CSV).write_text("代码,今日信号\n600900,MA金叉\n", encoding="utf-8-sig")
            (extra / "总表.md").write_text("今日无符合\n", encoding="utf-8")
            (extra / EXTRA_SUMMARY_MD).write_text("EXTRA-SIG 600900 布林下穿\n", encoding="utf-8")
            (extra / EXTRA_SUMMARY_CSV).write_text("代码,今日信号\n600900,布林下穿\n", encoding="utf-8-sig")

            dj_dest, dj_extra_dest = _copy_dianjin_tree(report, src)
            self.assertEqual((report / DAILY_MAIN_SUMMARY_MD).read_text(encoding="utf-8"), "MAIN-SIG 600900 MA金叉\n")
            self.assertEqual((report / EXTRA_SUMMARY_MD).read_text(encoding="utf-8"), "EXTRA-SIG 600900 布林下穿\n")
            self.assertTrue((report / DAILY_MAIN_SUMMARY_CSV).is_file())
            self.assertTrue((report / EXTRA_SUMMARY_CSV).is_file())
            self.assertEqual((dj_dest / MAIN_SUMMARY_MD).read_text(encoding="utf-8"), "MAIN-SIG 600900 MA金叉\n")
            self.assertEqual((dj_extra_dest / EXTRA_SUMMARY_MD).read_text(encoding="utf-8"), "EXTRA-SIG 600900 布林下穿\n")
            self.assertNotEqual(DAILY_MAIN_SUMMARY_MD, EXTRA_SUMMARY_MD)

    def test_copy_writes_empty_mark_when_source_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "dj_src"
            report = Path(tmp) / "20260818"
            report.mkdir()
            (src / "点金术").mkdir(parents=True)
            (src / "点金术" / "总表.md").write_text("x\n", encoding="utf-8")
            (src / "点金术extra").mkdir(parents=True)
            copy_summaries_to_daily_root(report, src / "点金术", src / "点金术extra")
            self.assertIn(EMPTY_MARK, (report / DAILY_MAIN_SUMMARY_MD).read_text(encoding="utf-8"))
            self.assertIn(EMPTY_MARK, (report / EXTRA_SUMMARY_MD).read_text(encoding="utf-8"))

    def test_extra_independent_when_main_has_hits(self):
        extra = _hit("600900", "长江电力", extra=True)
        main_only = _hit("601288", "农业银行")
        screen = ScreenResult(hits=[extra, main_only], extra=[extra], snapshot_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_reports(
                screen,
                out,
                stamp="20260818",
                enrich_info=[
                    {
                        "hit": extra,
                        "folder": "600900_长江电力",
                        "last_date": "2026-08-18",
                        "signals": [_sig("2026-08-18", "MACD金叉")],
                    },
                    {
                        "hit": main_only,
                        "folder": "601288_农业银行",
                        "last_date": "2026-08-18",
                        "signals": [_sig("2026-08-18", "RSI离开超卖")],
                    },
                ],
                extra_enrich_info=[
                    {
                        "hit": extra,
                        "folder": "600900_长江电力",
                        "last_date": "2026-08-18",
                        "signals": [_sig("2026-08-18", "MACD金叉")],
                    }
                ],
                screen_only=False,
                watchlist_codes=set(),
            )
            report = Path(tmp) / "日报"
            report.mkdir()
            _copy_dianjin_tree(report, out)
            daily_main = (report / DAILY_MAIN_SUMMARY_MD).read_text(encoding="utf-8")
            daily_extra = (report / EXTRA_SUMMARY_MD).read_text(encoding="utf-8")
            self.assertIn("601288", daily_main)
            self.assertIn("RSI离开超卖", daily_main)
            self.assertIn("600900", daily_extra)
            self.assertIn("MACD金叉", daily_extra)
            self.assertNotIn("601288", daily_extra)
            self.assertNotIn("RSI离开超卖", daily_extra)


if __name__ == "__main__":
    unittest.main()
