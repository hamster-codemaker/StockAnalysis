"""点金术日线缓存：收盘后作废盘前冻结的同日文件，停牌不循环。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dianjin.kline import (
    _bars_from_cache,
    _store_cache,
    fetch_qfq_bars,
    kline_cache_is_stale,
    reset_kline_breaker,
    shanghai_tzinfo,
)
from dianjin.source_pref import set_preference_path

TZ = shanghai_tzinfo()


def _dt(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


def _bars(last: str, n: int = 3) -> list[dict]:
    end = date.fromisoformat(last)
    out = []
    for i in range(n):
        day = (end - timedelta(days=n - 1 - i)).isoformat()
        out.append(
            {
                "date": day,
                "open": 1.0,
                "close": 10.0 + i,
                "high": 1.0,
                "low": 1.0,
                "volume": 1.0,
            }
        )
    out[-1]["date"] = last
    return out


def _write_cache(dest: Path, code: str, bars: list[dict], mtime: datetime, day: str) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{code}_{day}.json"
    path.write_text(
        json.dumps({"date": day, "code": code, "bars": bars}, ensure_ascii=False),
        encoding="utf-8",
    )
    ts = mtime.timestamp()
    os.utime(path, (ts, ts))
    return path


class TestKlineCacheStaleRule(unittest.TestCase):
    def test_after_close_preclose_mtime_ending_yesterday_is_stale(self):
        self.assertTrue(
            kline_cache_is_stale(
                _bars("2026-08-17"),
                mtime=_dt(2026, 8, 18, 13, 37),
                now=_dt(2026, 8, 18, 23, 15),
            )
        )

    def test_after_close_postclose_mtime_ending_yesterday_not_stale(self):
        self.assertFalse(
            kline_cache_is_stale(
                _bars("2026-08-17"),
                mtime=_dt(2026, 8, 18, 16, 0),
                now=_dt(2026, 8, 18, 23, 15),
            )
        )

    def test_weekend_friday_last_bar_not_stale(self):
        self.assertFalse(
            kline_cache_is_stale(
                _bars("2026-08-14"),
                mtime=_dt(2026, 8, 14, 16, 0),
                now=_dt(2026, 8, 15, 23, 15),
            )
        )

    def test_before_close_yesterday_last_bar_not_stale(self):
        self.assertFalse(
            kline_cache_is_stale(
                _bars("2026-08-17"),
                mtime=_dt(2026, 8, 18, 2, 17),
                now=_dt(2026, 8, 18, 13, 37),
            )
        )


class TestKlineCacheFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name)
        set_preference_path(Path(self.tmp.name) / "source_preference.yaml")
        self._dir = patch("dianjin.kline._cache_dir", return_value=self.cache)
        self._dir.start()
        reset_kline_breaker()

    def tearDown(self):
        reset_kline_breaker()
        self._dir.stop()
        set_preference_path(None)
        self.tmp.cleanup()

    def _freeze(self, moment: datetime):
        return patch("dianjin.kline.now_shanghai", return_value=moment)

    def test_after_close_cache_from_1337_refetches(self):
        code = "600900"
        stale = _bars("2026-08-17", n=3)
        fresh = _bars("2026-08-18", n=4)
        _write_cache(self.cache, code, stale, _dt(2026, 8, 18, 13, 37), "2026-08-18")
        with self._freeze(_dt(2026, 8, 18, 23, 15)):
            self.assertIsNone(_bars_from_cache(code))
            with (
                patch("dianjin.kline.fetch_tencent_bars", return_value=fresh) as tx,
                patch("dianjin.kline.fetch_sina_bars", side_effect=RuntimeError("sina")),
                patch("dianjin.kline.fetch_eastmoney_bars", side_effect=RuntimeError("em")),
            ):
                got = fetch_qfq_bars(code, limit=3)
        tx.assert_called()
        self.assertEqual(got[-1]["date"], "2026-08-18")
        self.assertEqual(len(got), 4)

    def test_after_close_halt_cache_at_1600_no_refetch_loop(self):
        code = "600000"
        halted = _bars("2026-08-17", n=3)
        _write_cache(self.cache, code, halted, _dt(2026, 8, 18, 16, 0), "2026-08-18")
        with self._freeze(_dt(2026, 8, 18, 23, 15)):
            with (
                patch("dianjin.kline.fetch_tencent_bars") as tx,
                patch("dianjin.kline.fetch_sina_bars", side_effect=RuntimeError("sina")),
                patch("dianjin.kline.fetch_eastmoney_bars", side_effect=RuntimeError("em")),
            ):
                first = fetch_qfq_bars(code, limit=3)
                second = fetch_qfq_bars(code, limit=3)
        tx.assert_not_called()
        self.assertEqual(first[-1]["date"], "2026-08-17")
        self.assertEqual(second[-1]["date"], "2026-08-17")

    def test_weekend_keeps_friday_cache(self):
        code = "000001"
        friday = _bars("2026-08-14", n=3)
        _write_cache(self.cache, code, friday, _dt(2026, 8, 14, 16, 5), "2026-08-15")
        with self._freeze(_dt(2026, 8, 15, 18, 0)):
            with (
                patch("dianjin.kline.fetch_tencent_bars") as tx,
                patch("dianjin.kline.fetch_sina_bars", side_effect=RuntimeError("sina")),
                patch("dianjin.kline.fetch_eastmoney_bars", side_effect=RuntimeError("em")),
            ):
                got = fetch_qfq_bars(code, limit=3)
        tx.assert_not_called()
        self.assertEqual(got[-1]["date"], "2026-08-14")

    def test_before_close_may_keep_yesterday(self):
        code = "601398"
        stale = _bars("2026-08-17", n=3)
        _write_cache(self.cache, code, stale, _dt(2026, 8, 18, 2, 17), "2026-08-18")
        with self._freeze(_dt(2026, 8, 18, 13, 37)):
            with (
                patch("dianjin.kline.fetch_tencent_bars") as tx,
                patch("dianjin.kline.fetch_sina_bars", side_effect=RuntimeError("sina")),
                patch("dianjin.kline.fetch_eastmoney_bars", side_effect=RuntimeError("em")),
            ):
                got = fetch_qfq_bars(code, limit=3)
        tx.assert_not_called()
        self.assertEqual(got[-1]["date"], "2026-08-17")

    def test_postclose_refetch_rewritten_cache_not_looped(self):
        code = "603856"
        stale = _bars("2026-08-17", n=3)
        _write_cache(self.cache, code, stale, _dt(2026, 8, 18, 2, 17), "2026-08-18")

        def fake_tx(code_arg, **kwargs):
            _store_cache(code_arg, stale)
            path = self.cache / f"{code_arg}_2026-08-18.json"
            ts = _dt(2026, 8, 18, 16, 0).timestamp()
            os.utime(path, (ts, ts))
            return stale

        with self._freeze(_dt(2026, 8, 18, 23, 15)):
            with (
                patch("dianjin.kline.fetch_tencent_bars", side_effect=fake_tx) as tx,
                patch("dianjin.kline.fetch_sina_bars", side_effect=RuntimeError("sina")),
                patch("dianjin.kline.fetch_eastmoney_bars", side_effect=RuntimeError("em")),
            ):
                first = fetch_qfq_bars(code, limit=3)
                second = fetch_qfq_bars(code, limit=3)
        self.assertEqual(tx.call_count, 1)
        self.assertEqual(first[-1]["date"], "2026-08-17")
        self.assertEqual(second[-1]["date"], "2026-08-17")


if __name__ == "__main__":
    unittest.main()
