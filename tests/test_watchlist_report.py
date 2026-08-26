"""自选股独立目录、综合分析、一键入口与日报布局。"""

from __future__ import annotations

import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dianjin.pipeline import DianjinRunResult
from dianjin.screen import ScreenResult
from launcher.paths import WATCHLIST_ANALYZE_FLAG, watchlist_analyze_argv
from launcher.settings import Settings
from launcher.watchlist_report import (
    COMBINED_MD,
    EMPTY_MARK,
    FUND_SUBDIR,
    STOCKS_SUBDIR,
    TECH_SUBDIR,
    WATCHLIST_DIR_NAME,
    WatchlistBundle,
    WatchlistStockRow,
    prune_legacy_date_root_watchlist,
    write_combined_analysis,
    write_empty_watchlist_tree,
    write_watchlist_tree,
)
from tech_analysis.report import StockResult
from tech_analysis.signals import MA_GOLDEN, Signal
from tech_analysis.watchlist import WatchItem


def _signal(day: str = "2026-08-18") -> Signal:
    return Signal(
        date=pd.Timestamp(day),
        signal_type=MA_GOLDEN,
        detail="MA8上穿MA24",
        close=10.5,
        metrics={"MA短": 10.2, "MA长": 10.0},
    )


def _tech_result(tmp: Path, *, ok: bool = True) -> StockResult:
    src = tmp / "tech_src" / "600900_测试股"
    src.mkdir(parents=True, exist_ok=True)
    (src / "分析报告.md").write_text("# 测试股 技术面分析\n", encoding="utf-8")
    (src / "技术分析.png").write_bytes(b"png")
    (src / "signals.csv").write_text("日期,类型\n2026-08-18,MA金叉\n", encoding="utf-8")
    (src / "日线指标.csv").write_text("date,close\n2026-08-18,10.5\n", encoding="utf-8")
    return StockResult(
        code="600900",
        name="测试股",
        ok=ok,
        error="" if ok else "K线失败",
        last_date="2026-08-18",
        last_close=10.5,
        signals=[_signal()],
        snapshot={
            "date": "2026-08-18",
            "close": 10.5,
            "ma_short": 10.2,
            "ma_long": 10.0,
            "ma_trend": 12.0,
            "dif": 0.1,
            "dea": 0.05,
            "hist": 0.1,
            "boll_upper": 11.0,
            "boll_mid": 10.4,
            "boll_lower": 9.8,
            "rsi": 55.0,
            "rsi2": 50.0,
            "rsi3": 48.0,
        },
        out_dir=src,
    )


def _fund_src(tmp: Path) -> Path:
    dest = tmp / "fund_src"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "财务分析.md").write_text("# 测试股 财务分析\n", encoding="utf-8")
    (dest / "财务数据.csv").write_text(
        "报告期,报告类型,营业收入(万元),营业收入同比(%),营业收入环比(%),"
        "归母净利润(万元),归母净利润同比(%),归母净利润环比(%),毛利率(%),净利率(%),ROE(%)\n"
        "2025-12-31,年报,120000,8.5,2.0,20000,6.0,1.0,30.0,15.0,12.0\n",
        encoding="utf-8-sig",
    )
    return dest


def _row(tmp: Path) -> WatchlistStockRow:
    tech = _tech_result(tmp)
    fund = _fund_src(tmp)
    latest = {
        "报告期": "2025-12-31",
        "报告类型": "年报",
        "营业收入(万元)": 120000,
        "营业收入同比(%)": 8.5,
        "营业收入环比(%)": 2.0,
        "归母净利润(万元)": 20000,
        "归母净利润同比(%)": 6.0,
        "归母净利润环比(%)": 1.0,
        "毛利率(%)": 30.0,
        "净利率(%)": 15.0,
        "ROE(%)": 12.0,
    }
    return WatchlistStockRow(
        code="600900",
        name="测试股",
        folder="600900_测试股",
        tech=tech,
        finance=latest,
        finance_ok=True,
        tech_src=tech.out_dir,
        fund_src=fund,
    )


def _bundle(tmp: Path, *, empty: bool = False) -> WatchlistBundle:
    if empty:
        return WatchlistBundle(
            empty=True,
            items=[],
            rows=[],
            wl_path=tmp / "watchlist.txt",
            stamp="2026-08-18 16:00",
            earnings=False,
        )
    row = _row(tmp)
    return WatchlistBundle(
        empty=False,
        items=[WatchItem("600900", "测试股")],
        rows=[row],
        wl_path=tmp / "watchlist.txt",
        stamp="2026-08-18 16:00",
        earnings=True,
    )


def _fake_dianjin(tmp: Path) -> DianjinRunResult:
    src = tmp / "dj_src"
    (src / "点金术").mkdir(parents=True)
    (src / "点金术" / "总表.md").write_text("点金术总表\n", encoding="utf-8")
    (src / "点金术" / "点金术.md").write_text("点金术名单\n", encoding="utf-8")
    (src / "点金术" / "技术信号汇总.md").write_text("今日无符合\n", encoding="utf-8")
    (src / "点金术extra").mkdir(parents=True)
    (src / "点金术extra" / "总表.md").write_text("今日无符合\n", encoding="utf-8")
    (src / "点金术extra" / "点金术extra.md").write_text("今日无符合\n", encoding="utf-8")
    (src / "点金术extra" / "点金术extra技术信号汇总.md").write_text("今日无符合\n", encoding="utf-8")
    return DianjinRunResult(returncode=0, out_dir=src, screen=ScreenResult())


class TestCombinedAnalysis(unittest.TestCase):
    def test_combined_md_from_tech_and_finance(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            row = _row(tmp)
            dest = tmp / COMBINED_MD
            write_combined_analysis(
                dest,
                code=row.code,
                name=row.name,
                stamp="2026-08-18 16:00",
                tech=row.tech,
                finance=row.finance,
                finance_ok=True,
                fund_md_exists=True,
                tech_md_exists=True,
            )
            text = dest.read_text(encoding="utf-8")
            self.assertIn("综合分析", text)
            self.assertIn("2025-12-31", text)
            self.assertIn("12.00 亿元", text)
            self.assertIn("2026-08-18", text)
            self.assertIn("10.50", text)
            self.assertIn("基本面/财务分析.md", text)
            self.assertIn("技术面/分析报告.md", text)
            self.assertIn("不是**点金术", text)
            self.assertIn("不估算合理估值", text)
            self.assertNotIn("目标价", text)

    def test_combined_md_says_missing_when_no_data(self):
        with tempfile.TemporaryDirectory() as raw:
            dest = Path(raw) / COMBINED_MD
            tech = StockResult(code="000001", name="空", ok=False, error="无K线")
            write_combined_analysis(
                dest,
                code="000001",
                name="空",
                stamp="t",
                tech=tech,
                finance={},
                finance_ok=False,
                finance_error="无财务数据",
            )
            text = dest.read_text(encoding="utf-8")
            self.assertIn("无财务数据", text)
            self.assertIn("无K线", text)
            self.assertIn("财务分析.md 未生成", text)


class TestWatchlistTree(unittest.TestCase):
    def test_writes_dedicated_layout(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            root = tmp / WATCHLIST_DIR_NAME
            write_watchlist_tree(root, _bundle(tmp))
            stock = root / STOCKS_SUBDIR / "600900_测试股"
            self.assertTrue((root / "总表.md").is_file())
            self.assertTrue((root / "总表.csv").is_file())
            self.assertTrue((stock / COMBINED_MD).is_file())
            self.assertTrue((stock / TECH_SUBDIR / "分析报告.md").is_file())
            self.assertTrue((stock / FUND_SUBDIR / "财务分析.md").is_file())
            table = (root / "总表.md").read_text(encoding="utf-8")
            self.assertIn("600900", table)
            self.assertIn("综合分析", table)
            self.assertNotIn("点金术要求", table)

    def test_empty_writes_no_match(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            root = tmp / WATCHLIST_DIR_NAME
            write_empty_watchlist_tree(root, stamp="t", wl_path=tmp / "watchlist.txt")
            text = (root / "总表.md").read_text(encoding="utf-8")
            self.assertIn(EMPTY_MARK, text)
            self.assertTrue((root / STOCKS_SUBDIR).is_dir())
            self.assertIn(EMPTY_MARK, (root / "技术面汇总.md").read_text(encoding="utf-8"))
            self.assertIn(EMPTY_MARK, (root / "基本面汇总.md").read_text(encoding="utf-8"))

    def test_prune_legacy_only_on_dated_root(self):
        with tempfile.TemporaryDirectory() as raw:
            dated = Path(raw) / "20260818"
            dated.mkdir()
            (dated / "技术面").mkdir()
            (dated / "基本面").mkdir()
            (dated / "技术面汇总.md").write_text("old", encoding="utf-8")
            other = Path(raw) / "data"
            other.mkdir()
            (other / "技术面").mkdir()
            prune_legacy_date_root_watchlist(dated)
            prune_legacy_date_root_watchlist(other)
            self.assertFalse((dated / "技术面").exists())
            self.assertFalse((dated / "基本面").exists())
            self.assertFalse((dated / "技术面汇总.md").exists())
            self.assertTrue((other / "技术面").is_dir())


class TestDailyReportLayout(unittest.TestCase):
    def _run_daily(self, tmp: Path, bundle: WatchlistBundle, dj_called: list):
        report = tmp / "20260818"
        report.mkdir()
        (report / "技术面").mkdir()
        (report / "基本面").mkdir()

        def fake_watch(*, watch_roots=None, **_kwargs):
            from launcher.watchlist_report import write_watchlist_tree

            for root in watch_roots or []:
                write_watchlist_tree(root, bundle)
            bundle.roots = list(watch_roots or [])
            return bundle

        def fake_dj(_cfg):
            dj_called.append(True)
            return _fake_dianjin(tmp)

        last = tmp / "last_report.json"
        data_watch = tmp / "data_watchlist"
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            with (
                patch("launcher.daily_report.chdir_project_root", return_value=tmp),
                patch("launcher.daily_report.load_settings", return_value=Settings(daily_update=True)),
                patch("launcher.daily_report.dated_report_dir", return_value=report),
                patch("launcher.daily_report.watchlist_output_dir", return_value=data_watch),
                patch("launcher.daily_report.run_watchlist_full_analysis", side_effect=fake_watch),
                patch("launcher.daily_report.last_report_path", return_value=last),
                patch("stock_screener.config.load_config", return_value={}),
                patch("dianjin.pipeline.run_dianjin", side_effect=fake_dj),
            ):
                from launcher.daily_report import run_daily_report

                code = run_daily_report()
        finally:
            os.chdir(cwd)
        return report, code

    def test_watchlist_folder_separate_from_dianjin(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            called: list = []
            report, code = self._run_daily(tmp, _bundle(tmp), called)
            self.assertEqual(code, 0)
            self.assertTrue(called)
            self.assertTrue((report / WATCHLIST_DIR_NAME / "总表.md").is_file())
            self.assertTrue((report / WATCHLIST_DIR_NAME / STOCKS_SUBDIR / "600900_测试股" / COMBINED_MD).is_file())
            self.assertTrue((report / "点金术" / "总表.md").is_file())
            self.assertTrue((report / "点金术" / "技术信号汇总.md").is_file())
            self.assertTrue((report / "点金术技术信号汇总.md").is_file())
            self.assertTrue((report / "点金术extra").is_dir())
            self.assertTrue((report / "点金术extra技术信号汇总.md").is_file())
            self.assertFalse((report / "技术面").exists())
            self.assertFalse((report / "基本面").exists())
            overview = (report / "总览.md").read_text(encoding="utf-8")
            self.assertIn("## 自选股", overview)
            self.assertIn("## 点金术", overview)
            self.assertIn(f"{WATCHLIST_DIR_NAME}/总表.md", overview)
            self.assertIn("点金术技术信号汇总.md", overview)
            self.assertIn("点金术extra技术信号汇总.md", overview)
            self.assertIn("综合分析", overview)
            self.assertNotIn("](技术面/", overview)
            self.assertNotIn("](基本面/", overview)

    def test_empty_watchlist_still_runs_dianjin(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            called: list = []
            report, code = self._run_daily(tmp, _bundle(tmp, empty=True), called)
            self.assertEqual(code, 0)
            self.assertTrue(called, "空自选股时仍应调用点金术")
            text = (report / WATCHLIST_DIR_NAME / "总表.md").read_text(encoding="utf-8")
            self.assertIn(EMPTY_MARK, text)
            self.assertTrue((report / "点金术" / "总表.md").is_file())
            overview = (report / "总览.md").read_text(encoding="utf-8")
            self.assertIn(EMPTY_MARK, overview)


class TestWatchlistPipeline(unittest.TestCase):
    def test_full_analysis_calls_tech_and_finance(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            dest = tmp / WATCHLIST_DIR_NAME
            tech = _tech_result(tmp)
            fund = _fund_src(tmp)
            docs = tmp / "docs" / "600900_测试股"
            (docs / "财务分析").mkdir(parents=True)
            for name in ("财务分析.md", "财务数据.csv"):
                src = fund / name
                if src.is_file():
                    (docs / "财务分析" / name).write_bytes(src.read_bytes())

            def fake_analyze_one(code, name, cfg, limiter, out_root):
                return tech

            def fake_finance(code, name, dest_dir, cfg):
                return True

            with (
                patch("launcher.watchlist_report.chdir_project_root", return_value=tmp),
                patch("launcher.watchlist_report.load_settings", return_value=Settings(daily_update=True)),
                patch("launcher.watchlist_report.ensure_watchlist", return_value=tmp / "watchlist.txt"),
                patch("tech_analysis.watchlist.load_watchlist", return_value=[WatchItem("600900", "测试股")]),
                patch("tech_analysis.config.load_config", return_value={}),
                patch("stock_screener.config.load_config", return_value={}),
                patch("tech_analysis.network.disable_proxies"),
                patch("tech_analysis.network.enable_browser_tls"),
                patch("tech_analysis.main.analyze_one", side_effect=fake_analyze_one),
                patch("stock_screener.finance.analyze_stock_finance", side_effect=fake_finance),
                patch("tech_analysis.market.resolve_name", side_effect=lambda code, name, *a, **k: name or code),
                patch("tech_analysis.market.remember_name"),
                patch("launcher.watchlist_report.docs_dir", return_value=tmp / "docs"),
                patch("launcher.watchlist_report.tech_output_dir", return_value=tmp / "tech_out"),
                patch("tech_analysis.report.write_summary"),
            ):
                from launcher.watchlist_report import run_watchlist_full_analysis

                bundle = run_watchlist_full_analysis(watch_roots=[dest], stamp="t", settings=Settings())
            self.assertFalse(bundle.empty)
            combined = dest / STOCKS_SUBDIR / "600900_测试股" / COMBINED_MD
            self.assertTrue(combined.is_file())
            text = combined.read_text(encoding="utf-8")
            self.assertIn("2025-12-31", text)
            self.assertIn("技术面", text)


class TestCliAndGuiEntry(unittest.TestCase):
    def test_cli_flag_calls_same_pipeline(self):
        from launcher.suite import HELP, main

        self.assertIn(WATCHLIST_ANALYZE_FLAG, HELP)
        self.assertIn("watchlist", HELP)
        with patch("launcher.watchlist_report.main", return_value=0) as mocked:
            self.assertEqual(main(["--watchlist-analyze"]), 0)
            mocked.assert_called()
        with patch("launcher.watchlist_report.main", return_value=0) as mocked:
            self.assertEqual(main(["watchlist"]), 0)
            mocked.assert_called()

    def test_gui_button_uses_same_flag(self):
        from launcher.gui import SuiteApp

        argv = watchlist_analyze_argv()
        self.assertIn(WATCHLIST_ANALYZE_FLAG, argv)
        self.assertTrue(hasattr(SuiteApp, "_start_watchlist_analyze"))
        self.assertTrue(hasattr(SuiteApp, "_watchlist_analyze_argv"))
        src = inspect.getsource(SuiteApp._build_watchlist)
        self.assertIn("分析全部自选股", src)
        method = inspect.getsource(SuiteApp._start_watchlist_analyze)
        self.assertIn("_watchlist_analyze_argv", method)


if __name__ == "__main__":
    unittest.main()
