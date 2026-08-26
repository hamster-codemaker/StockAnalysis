"""点金术规则、空名单报告与中等规模联调（不全市场拉 MA120）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dianjin.report import (
    EMPTY_MARK,
    WATCHLIST_BADGE,
    extra_dir,
    main_dir,
    prune_stale_stock_dirs,
    write_empty_reports,
    write_reports,
)
from dianjin.rules import (
    classify_lists,
    is_bse,
    is_st_or_delist,
    passes_dividend,
    passes_ma_discount,
    passes_pe,
    passes_universe,
    passes_value,
)
from dianjin.screen import DianjinHit, ScreenResult, screen_market
from dianjin.pipeline import run_dianjin
from dianjin.watchlist_mark import (
    load_watchlist_codes,
    stock_folder_name,
)


def _row(**kwargs):
    base = dict(
        dividend=3.5,
        pe_dyn=10.0,
        pe_static=11.0,
        pe_ttm=12.0,
        close=8.0,
        ma120=10.0,
    )
    base.update(kwargs)
    return base


class TestDianjinRules(unittest.TestCase):
    def test_pass_main_not_extra(self):
        main, extra = classify_lists(**_row(dividend=3.5, close=8.7, ma120=10.0))
        self.assertTrue(main)
        self.assertFalse(extra)
        self.assertLess(8.7, 0.88 * 10.0)
        self.assertFalse(8.7 < 0.82 * 10.0)

    def test_pass_extra_subset(self):
        main, extra = classify_lists(**_row(dividend=4.1, close=8.0, ma120=10.0))
        self.assertTrue(main)
        self.assertTrue(extra)

    def test_extra_requires_same_pe(self):
        main, extra = classify_lists(**_row(dividend=5.0, pe_dyn=21.0, close=8.0, ma120=10.0))
        self.assertFalse(main)
        self.assertFalse(extra)

    def test_strict_dividend(self):
        self.assertFalse(passes_dividend(3.0, 3.0))
        self.assertTrue(passes_dividend(3.01, 3.0))
        self.assertFalse(passes_dividend(None, 3.0))
        self.assertFalse(passes_dividend("-", 3.0))
        self.assertFalse(passes_dividend("nan", 3.0))

    def test_strict_pe(self):
        self.assertTrue(passes_pe(19.99, 10, 10, 20))
        self.assertFalse(passes_pe(20, 10, 10, 20))
        self.assertFalse(passes_pe(0, 10, 10, 20))
        self.assertFalse(passes_pe(-1, 10, 10, 20))
        self.assertFalse(passes_pe(None, 10, 10, 20))
        self.assertFalse(passes_pe("-", 10, 10, 20))
        self.assertFalse(passes_pe(10, None, 10, 20))
        self.assertFalse(passes_pe(10, 10, "abc", 20))

    def test_strict_ma(self):
        self.assertTrue(passes_ma_discount(8.79, 10.0, 0.88))
        self.assertFalse(passes_ma_discount(8.81, 10.0, 0.88))
        self.assertFalse(passes_ma_discount(8.8, 10.0, 0.88))
        self.assertFalse(passes_ma_discount(None, 10.0, 0.88))
        self.assertFalse(passes_ma_discount(8.0, 0, 0.88))

    def test_missing_value_fails_snapshot_stage(self):
        self.assertFalse(passes_value(3.5, None, 10, 10))
        self.assertFalse(passes_value(None, 10, 10, 10))
        self.assertTrue(passes_value(3.5, 10, 10, 10))

    def test_universe_st_and_bse(self):
        self.assertTrue(is_st_or_delist("ST宁科"))
        self.assertTrue(is_st_or_delist("*ST海航"))
        self.assertTrue(is_st_or_delist("退市整理"))
        self.assertTrue(is_bse("430047"))
        self.assertTrue(is_bse("830799"))
        self.assertFalse(passes_universe("600000", "ST示例", exclude_st=True, include_bse=False))
        self.assertFalse(passes_universe("430047", "贝特瑞", exclude_st=True, include_bse=False))
        self.assertTrue(passes_universe("600900", "长江电力", exclude_st=True, include_bse=False))

    def test_empty_classify(self):
        main, extra = classify_lists(**_row(dividend=0.1, close=20, ma120=10))
        self.assertFalse(main)
        self.assertFalse(extra)


class TestEmptyReports(unittest.TestCase):
    def test_empty_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_empty_reports(out, stamp="20260817", error="")
            main = main_dir(out)
            extra = extra_dir(out)
            for path in (
                out / "总表.md",
                main / "总表.md",
                main / "点金术.md",
                main / "点金术.csv",
                extra / "总表.md",
                extra / "点金术extra.md",
                extra / "点金术extra.csv",
            ):
                self.assertTrue(path.is_file(), str(path))
            self.assertTrue((main / "个股").is_dir())
            self.assertTrue((extra / "个股").is_dir())
            self.assertIn(EMPTY_MARK, (main / "点金术.md").read_text(encoding="utf-8"))
            self.assertIn(EMPTY_MARK, (extra / "点金术extra.md").read_text(encoding="utf-8"))
            self.assertIn(EMPTY_MARK, (out / "总表.md").read_text(encoding="utf-8"))
            self.assertIn(EMPTY_MARK, (main / "技术信号汇总.md").read_text(encoding="utf-8"))
            self.assertIn(
                EMPTY_MARK,
                (extra / "点金术extra技术信号汇总.md").read_text(encoding="utf-8"),
            )
            self.assertTrue((main / "技术信号汇总.csv").is_file())
            self.assertTrue((extra / "点金术extra技术信号汇总.csv").is_file())

    def test_empty_with_snapshot_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            screen = ScreenResult(error="全市场估值快照失败（未放宽市盈率规则）：unit-test")
            write_reports(screen, out, stamp="20260817", enrich_info=[], screen_only=True)
            text = (main_dir(out) / "总表.md").read_text(encoding="utf-8")
            self.assertIn(EMPTY_MARK, text)
            self.assertIn("未放宽市盈率规则", text)
            self.assertIn(EMPTY_MARK, (extra_dir(out) / "总表.md").read_text(encoding="utf-8"))


class TestEmptyThresholdPath(unittest.TestCase):
    def test_high_dividend_threshold_yields_empty(self):
        rows = [
            {
                "code": "600000",
                "name": "浦发银行",
                "close": 8.0,
                "pe_dyn": 5.0,
                "pe_static": 6.0,
                "pe_ttm": 7.0,
                "dividend": 5.0,
            }
        ]
        cfg = {"dianjin": {"dividend_min": 999.0, "extra_dividend_min": 999.0, "include_bse": False}}
        result = screen_market(cfg, snapshot_rows=rows, hist_limit=1)
        self.assertTrue(result.snapshot_ok)
        self.assertEqual(result.value_pass, 0)
        self.assertEqual(result.ma_fetched, 0)
        self.assertEqual(result.hits, [])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_reports(result, out, stamp="20260817", screen_only=True)
            self.assertIn(EMPTY_MARK, (main_dir(out) / "点金术.md").read_text(encoding="utf-8"))
            self.assertTrue((extra_dir(out) / "总表.md").is_file())


class TestPruneAndExtraFolder(unittest.TestCase):
    def test_prune_hits_and_extra_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main_stocks = main_dir(root) / "个股"
            extra_stocks = extra_dir(root) / "个股"
            (main_stocks / "600900_长江电力").mkdir(parents=True)
            (main_stocks / "600000_浦发银行").mkdir(parents=True)
            (extra_stocks / "600900_长江电力").mkdir(parents=True)
            (extra_stocks / "600000_浦发银行").mkdir(parents=True)
            (main_stocks / "600900_长江电力" / "个股报告.md").write_text("keep", encoding="utf-8")
            deleted_main, kept_main = prune_stale_stock_dirs(main_dir(root), ["600900"], label="点金术")
            deleted_extra, kept_extra = prune_stale_stock_dirs(extra_dir(root), [], label="点金术extra")
            self.assertEqual(deleted_main, ["600000"])
            self.assertEqual(kept_main, ["600900"])
            self.assertIn("600000", deleted_extra)
            self.assertEqual(kept_extra, [])
            self.assertTrue((main_stocks / "600900_长江电力").is_dir())
            self.assertFalse((main_stocks / "600000_浦发银行").exists())
            self.assertFalse((extra_stocks / "600900_长江电力").exists())
            self.assertFalse((extra_stocks / "600000_浦发银行").exists())

    def test_prune_matches_code_with_watchlist_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main_stocks = main_dir(root) / "个股"
            extra_stocks = extra_dir(root) / "个股"
            (main_stocks / "600900_长江电力_自选").mkdir(parents=True)
            (main_stocks / "600000_浦发银行_自选").mkdir(parents=True)
            (extra_stocks / "600900_长江电力_自选").mkdir(parents=True)
            deleted_main, kept_main = prune_stale_stock_dirs(main_dir(root), ["600900"], label="点金术")
            deleted_extra, kept_extra = prune_stale_stock_dirs(
                extra_dir(root),
                ["600900"],
                label="点金术extra",
                preferred_names={"600900": "600900_长江电力_自选"},
            )
            self.assertEqual(deleted_main, ["600000"])
            self.assertEqual(kept_main, ["600900"])
            self.assertEqual(deleted_extra, [])
            self.assertEqual(kept_extra, ["600900"])
            self.assertTrue((main_stocks / "600900_长江电力_自选").is_dir())
            self.assertFalse((main_stocks / "600000_浦发银行_自选").exists())
            self.assertTrue((extra_stocks / "600900_长江电力_自选").is_dir())

    def test_prune_collapses_old_name_when_preferred_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            stocks = main_dir(Path(tmp)) / "个股"
            (stocks / "600900_长江电力").mkdir(parents=True)
            (stocks / "600900_长江电力_自选").mkdir(parents=True)
            prune_stale_stock_dirs(
                main_dir(Path(tmp)),
                ["600900"],
                preferred_names={"600900": "600900_长江电力_自选"},
            )
            self.assertTrue((stocks / "600900_长江电力_自选").is_dir())
            self.assertFalse((stocks / "600900_长江电力").exists())

    def test_empty_run_clears_stale_and_keeps_extra_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            stale = main_dir(out) / "个股" / "600001_旧股"
            stale.mkdir(parents=True)
            (stale / "个股报告.md").write_text("old", encoding="utf-8")
            extra_stale = extra_dir(out) / "个股" / "600001_旧股"
            extra_stale.mkdir(parents=True)
            result = run_dianjin(
                {"dianjin": {"dividend_min": 999.0, "extra_dividend_min": 999.0}},
                screen_only=True,
                snapshot_rows=[
                    {
                        "code": "600000",
                        "name": "浦发银行",
                        "close": 8.0,
                        "pe_dyn": 5.0,
                        "pe_static": 6.0,
                        "pe_ttm": 7.0,
                        "dividend": 5.0,
                    }
                ],
                out_dir=out,
            )
            self.assertEqual(result.screen.hits, [])
            self.assertFalse(stale.exists())
            self.assertFalse(extra_stale.exists())
            self.assertTrue((extra_dir(out) / "总表.md").is_file())
            self.assertIn(EMPTY_MARK, (extra_dir(out) / "总表.md").read_text(encoding="utf-8"))


class TestGuiArgv(unittest.TestCase):
    def test_dianjin_argv(self):
        from app_gui.commands import GuiOptions, TASKS, build_argv

        self.assertIn(("dianjin", "点金术"), TASKS)
        argv = build_argv(GuiOptions(command="dianjin", codes="600900", limit="2"))
        self.assertIn("dianjin", argv)
        self.assertIn("--codes", argv)
        self.assertIn("600900", argv)
        self.assertIn("--limit", argv)
        self.assertIn("2", argv)
        self.assertNotIn("--hist-limit", argv)
        self.assertNotIn("--screen-only", argv)


def _hit(code: str, name: str, extra: bool = False) -> DianjinHit:
    return DianjinHit(
        code=code,
        name=name,
        close=8.0,
        ma120=10.0,
        close_ma_ratio=0.8,
        dividend=4.5 if extra else 3.5,
        pe_dyn=10.0,
        pe_static=11.0,
        pe_ttm=12.0,
        is_extra=extra,
    )


class TestWatchlistAnnotation(unittest.TestCase):
    def test_folder_naming(self):
        self.assertEqual(stock_folder_name("600900", "长江电力", False), "600900_长江电力")
        self.assertEqual(stock_folder_name("600900", "长江电力", True), "600900_长江电力_自选")
        self.assertTrue(stock_folder_name("600900", "长江电力", True).startswith("600900_"))

    def test_empty_watchlist_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlist.txt"
            path.write_text("# empty\n\n", encoding="utf-8")
            self.assertEqual(load_watchlist_codes(path), set())

    def test_missing_watchlist_no_crash(self):
        missing = Path(tempfile.gettempdir()) / "no_such_watchlist_sa_test.txt"
        if missing.exists():
            missing.unlink()
        self.assertEqual(load_watchlist_codes(missing), set())

    def test_overview_marks_watchlist_and_not_universe(self):
        hits = [_hit("600900", "长江电力", extra=True), _hit("600000", "浦发银行")]
        screen = ScreenResult(hits=hits, extra=[hits[0]], snapshot_ok=True, snapshot_count=2)
        info = [
            {
                "hit": hits[0],
                "folder": "600900_长江电力_自选",
                "in_watchlist": True,
                "recent_signals": "无",
            },
            {
                "hit": hits[1],
                "folder": "600000_浦发银行",
                "in_watchlist": False,
                "recent_signals": "无",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_reports(
                screen,
                out,
                stamp="20260817",
                enrich_info=info,
                extra_enrich_info=[info[0]],
                screen_only=False,
                watchlist_codes={"600900"},
            )
            main_md = (main_dir(out) / "点金术.md").read_text(encoding="utf-8")
            extra_md = (extra_dir(out) / "点金术extra.md").read_text(encoding="utf-8")
            csv = (main_dir(out) / "点金术.csv").read_text(encoding="utf-8-sig")
            self.assertIn(WATCHLIST_BADGE, main_md)
            self.assertIn("| 自选 |", main_md)
            self.assertIn("600900_长江电力_自选", main_md)
            self.assertIn("600000_浦发银行", main_md)
            self.assertNotIn("600000_浦发银行_自选", main_md)
            self.assertIn("600900_长江电力_自选", extra_md)
            self.assertIn(WATCHLIST_BADGE, csv)
            self.assertIn("其中自选股：1 只", main_md)
            self.assertTrue((extra_dir(out) / "总表.md").is_file())

    def test_empty_watchlist_no_marks(self):
        hits = [_hit("600900", "长江电力")]
        screen = ScreenResult(hits=hits, extra=[], snapshot_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_reports(
                screen,
                out,
                stamp="20260817",
                screen_only=True,
                watchlist_codes=set(),
            )
            text = (main_dir(out) / "点金术.md").read_text(encoding="utf-8")
            csv = (main_dir(out) / "点金术.csv").read_text(encoding="utf-8-sig")
            self.assertIn("其中自选股：0 只", text)
            self.assertIn("600900", text)
            self.assertIn("长江电力", text)
            self.assertRegex(csv, r"600900,长江电力,")
            self.assertNotRegex(csv, r"600900,长江电力,自选")

    def test_empty_dianjin_with_watchlist_no_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_reports(
                ScreenResult(),
                out,
                stamp="20260817",
                screen_only=True,
                watchlist_codes={"600900"},
            )
            self.assertIn(EMPTY_MARK, (main_dir(out) / "点金术.md").read_text(encoding="utf-8"))
            self.assertTrue((extra_dir(out) / "总表.md").is_file())

    def test_watchlist_does_not_filter_screen_universe(self):
        from unittest.mock import patch

        rows = [
            {
                "code": "600900",
                "name": "长江电力",
                "close": 8.0,
                "pe_dyn": 5.0,
                "pe_static": 6.0,
                "pe_ttm": 7.0,
                "dividend": 5.0,
            },
            {
                "code": "600000",
                "name": "浦发银行",
                "close": 8.0,
                "pe_dyn": 5.0,
                "pe_static": 6.0,
                "pe_ttm": 7.0,
                "dividend": 5.0,
            },
        ]

        def fake_ma(code, **kwargs):
            return 8.0, 10.0, 160

        with patch("dianjin.screen.fetch_close_and_ma120", fake_ma):
            result = screen_market(
                {"dianjin": {"kline_rate_limit": 0.0}},
                snapshot_rows=rows,
            )
        codes = {h.code for h in result.hits}
        self.assertEqual(codes, {"600900", "600000"})
        self.assertEqual({h.code for h in result.extra}, {"600900", "600000"})


class TestLiveSmoke(unittest.TestCase):
    """全市场快照可以跑；禁止给约 5000 只股票拉 MA120。"""

    @classmethod
    def setUpClass(cls):
        import os

        os.chdir(ROOT)

    def test_screen_only_hist_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_dianjin(
                None,
                screen_only=True,
                hist_limit=30,
                out_dir=tmp,
            )
            self.assertEqual(result.returncode, 0)
            self.assertTrue(result.screen.snapshot_ok, result.screen.error)
            self.assertGreater(result.screen.snapshot_count, 1000)
            self.assertLessEqual(result.screen.ma_fetched, 30)
            self.assertTrue((Path(tmp) / "总表.md").is_file())
            self.assertTrue((main_dir(Path(tmp)) / "点金术.md").is_file())
            self.assertTrue((extra_dir(Path(tmp)) / "点金术extra.md").is_file())
            if not result.screen.hits:
                self.assertIn(EMPTY_MARK, (main_dir(Path(tmp)) / "点金术.md").read_text(encoding="utf-8"))
                self.assertIn(EMPTY_MARK, (extra_dir(Path(tmp)) / "总表.md").read_text(encoding="utf-8"))


class TestScreenYieldColumn(unittest.TestCase):
    def test_list_has_single_f133_yield(self):
        from dianjin.rules import classify_lists

        main, extra = classify_lists(
            dividend=1.12,
            pe_dyn=18.0,
            pe_static=13.0,
            pe_ttm=14.0,
            close=27.0,
            ma120=36.0,
        )
        self.assertFalse(main)
        self.assertFalse(extra)
        hit = _hit("300850", "新强联", extra=True)
        hit.dividend = 1.12
        screen = ScreenResult(hits=[hit], extra=[hit], snapshot_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_reports(screen, out, stamp="20260818", screen_only=True, watchlist_codes=set())
            csv = (main_dir(out) / "点金术.csv").read_text(encoding="utf-8-sig")
            md = (main_dir(out) / "点金术.md").read_text(encoding="utf-8")
            self.assertIn("股息率%", csv)
            self.assertNotIn("股息率(东财)%", csv)
            self.assertNotIn("股息率(同花顺)%", csv)
            self.assertIn("1.12", csv)
            self.assertNotIn("10.73", csv)
            self.assertIn("同花顺股息率TTM", md)
            self.assertNotIn("只按东财 f183", md)


if __name__ == "__main__":
    unittest.main()
