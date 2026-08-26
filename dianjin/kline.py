"""前复权日线与 MA120：腾讯。不再请求新浪或东财 push2his。

实测无现成 MA120 字段（见 docs），用日线收盘价计算。
筛选只需 ≥120 根；个股图展示最近 120 个交易日并叠加 MA120，因此默认一次取约 250 根做预热。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any

from dianjin.em_cluster import (
    CHROME_UA,
    ClusterBreaker,
    disable_proxy_env,
    impersonated_get,
    try_hosts,
)
from dianjin.rules import to_float
from dianjin.source_pref import SourceBreaker, promote_backup, source_order

log = logging.getLogger("dianjin")

KLINE_PATH = "/api/qt/stock/kline/get"
KLINE_HOSTS = (
    "https://push2his.eastmoney.com",
    "https://82.push2his.eastmoney.com",
    "https://88.push2his.eastmoney.com",
    "https://33.push2his.eastmoney.com",
    "https://63.push2his.eastmoney.com",
    "https://7.push2his.eastmoney.com",
)
UT = "fa5fd1943c7b386f172d6893dbfba10b"
_HEADERS = {
    "User-Agent": CHROME_UA,
    "Referer": "https://quote.eastmoney.com/",
}
_TX_HEADERS = {
    "User-Agent": CHROME_UA,
    "Referer": "https://gu.qq.com/",
}

KLINE_BREAKER = ClusterBreaker(
    "push2his",
    KLINE_HOSTS,
    trip_after=2,
    hard_fail_host_cap=2,
    announce_message="东财 K 线集群不可达，本轮不再请求 push2his",
)
KLINE_SOURCE_BREAKER = SourceBreaker("kline")

_TQDM_SILENCED = False
# code -> (calendar day, bars, written_at Shanghai)
_MEM_CACHE: dict[str, tuple[str, list[dict[str, Any]], datetime]] = {}
_OBSERVED_SESSION: date | None = None
MARKET_CLOSE = time(15, 15)


def shanghai_tzinfo() -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Shanghai")  # type: ignore[return-value]
    except Exception:
        return timezone(timedelta(hours=8))


def now_shanghai() -> datetime:
    return datetime.now(shanghai_tzinfo())


def last_bar_date(bars: list[dict[str, Any]]) -> date | None:
    if not bars:
        return None
    raw = str(bars[-1].get("date") or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _as_shanghai(moment: datetime) -> datetime:
    tz = shanghai_tzinfo()
    if moment.tzinfo is None:
        return moment.replace(tzinfo=tz)
    return moment.astimezone(tz)


def _session_date(now: datetime) -> date:
    if _OBSERVED_SESSION is not None:
        return _OBSERVED_SESSION
    return now.date()


def _note_observed_session(bars: list[dict[str, Any]], today: date) -> None:
    global _OBSERVED_SESSION
    last = last_bar_date(bars)
    if last is not None and last == today:
        _OBSERVED_SESSION = last


def kline_cache_is_stale(
    bars: list[dict[str, Any]],
    *,
    mtime: datetime | None = None,
    now: datetime | None = None,
    session_date: date | None = None,
) -> bool:
    """Same-day kline cache must be refetched after close when all hold:

    - Shanghai calendar today is a weekday (holiday extra refetch is OK)
    - local time is at/after 15:15 Asia/Shanghai
    - last bar date < session date (observed post-close last==today, else calendar today)
    - cache mtime is before today's 15:15 (pre-close / pre-open freeze)

    Weekend Friday bars are kept. Intraday (before 15:15) yesterday last bar is kept.
    Post-close rewrite with last still < today (halt / source delay) is not stale.
    """
    clock = _as_shanghai(now) if now is not None else now_shanghai()
    today = clock.date()
    if today.weekday() >= 5:
        return False
    close_at = datetime.combine(today, MARKET_CLOSE, tzinfo=clock.tzinfo)
    if clock < close_at:
        return False
    last = last_bar_date(bars)
    expected = session_date if session_date is not None else _session_date(clock)
    if last is not None and last >= expected:
        return False
    if mtime is None:
        return True
    written = _as_shanghai(mtime)
    return written < close_at


def reset_kline_breaker() -> None:
    global _OBSERVED_SESSION
    KLINE_BREAKER.reset()
    KLINE_SOURCE_BREAKER.reset()
    _MEM_CACHE.clear()
    _OBSERVED_SESSION = None


def clear_bar_cache(code: str | None = None) -> None:
    dest = _cache_dir()
    if code is None:
        _MEM_CACHE.clear()
        if dest.is_dir():
            for path in dest.glob("*.json"):
                try:
                    path.unlink()
                except OSError:
                    pass
        return
    key = str(code).zfill(6)
    _MEM_CACHE.pop(key, None)
    _MEM_CACHE.pop(str(code), None)
    path = dest / f"{key}_{_today()}.json"
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


def _silence_akshare_tqdm() -> None:
    global _TQDM_SILENCED
    os.environ["TQDM_DISABLE"] = "1"
    os.environ["AKSHARE_USE_TQDM"] = "0"
    if _TQDM_SILENCED:
        return
    _TQDM_SILENCED = True
    try:
        from functools import partialmethod
        from tqdm import tqdm

        tqdm.__init__ = partialmethod(tqdm.__init__, disable=True)
    except Exception:
        pass
    try:
        import akshare.utils.tqdm as ak_tqdm

        ak_tqdm.get_tqdm = lambda enable=False: (lambda iterable, *args, **kwargs: iterable)
    except Exception:
        pass


def secid(code: str) -> str:
    text = str(code).zfill(6)
    if text.startswith(("6", "9")):
        return f"1.{text}"
    return f"0.{text}"


def market_symbol(code: str) -> str:
    text = str(code).zfill(6)
    if text.startswith(("6", "9")):
        return f"sh{text}"
    if text.startswith(("4", "8")):
        return f"bj{text}"
    return f"sz{text}"


def _impersonated_get(url: str, params: dict[str, str], timeout: float):
    return impersonated_get(url, params, timeout, _HEADERS)


def _tx_get(url: str, params: dict[str, str] | None, timeout: float):
    from dianjin.http_public import public_get

    return public_get(url, params=params, headers=_TX_HEADERS, timeout=timeout)


def _cache_dir() -> Path:
    try:
        from launcher.paths import project_root

        root = project_root()
    except Exception:
        root = Path.cwd()
    return root / "data" / "cache" / "kline"


def _today() -> str:
    return now_shanghai().date().isoformat()


def _file_mtime_shanghai(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=shanghai_tzinfo())
    except OSError:
        return None


def _bars_from_cache(code: str) -> list[dict[str, Any]] | None:
    day = _today()
    path = _cache_dir() / f"{code}_{day}.json"
    bars: list[dict[str, Any]] | None = None
    written: datetime | None = None
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            loaded = payload.get("bars") or []
            if loaded:
                bars = loaded
                written = _file_mtime_shanghai(path)
        except Exception:
            bars = None
    if not bars:
        hit = _MEM_CACHE.get(code)
        if hit and hit[0] == day:
            bars = hit[1]
            written = hit[2] if len(hit) > 2 else None
    if not bars:
        return None
    if kline_cache_is_stale(bars, mtime=written):
        _MEM_CACHE.pop(code, None)
        return None
    _note_observed_session(bars, now_shanghai().date())
    _MEM_CACHE[code] = (day, bars, written or now_shanghai())
    return bars


def _store_cache(code: str, bars: list[dict[str, Any]]) -> None:
    now = now_shanghai()
    _note_observed_session(bars, now.date())
    _MEM_CACHE[code] = (_today(), bars, now)
    dest = _cache_dir()
    try:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / f"{code}_{_today()}.json").write_text(
            json.dumps({"date": _today(), "code": code, "bars": bars}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _row(date_s: str, open_: float, close: float, high: float, low: float, volume: float) -> dict[str, Any]:
    return {
        "date": date_s[:10],
        "open": open_,
        "close": close,
        "high": high,
        "low": low,
        "volume": volume,
    }


def _closes(bars: list[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    for bar in bars:
        price = to_float(bar.get("close"))
        if price is not None:
            out.append(price)
    return out


def _parse_tx_payload(text: str, symbol: str) -> list[dict[str, Any]]:
    body = text
    if "={" in body:
        body = body[body.find("={") + 1 :]
    payload = json.loads(body)
    data = (payload.get("data") or {}).get(symbol) or {}
    series = data.get("qfqday") or data.get("day") or []
    bars: list[dict[str, Any]] = []
    for item in series:
        if not item or len(item) < 5:
            continue
        close = to_float(item[2])
        if close is None:
            continue
        bars.append(
            _row(
                str(item[0]),
                to_float(item[1]) or close,
                close,
                to_float(item[3]) or close,
                to_float(item[4]) or close,
                to_float(item[5]) or 0.0,
            )
        )
    return bars


def fetch_tencent_bars(code: str, *, limit: int = 250, timeout: float = 12.0) -> list[dict[str, Any]]:
    """腾讯前复权日线，一次请求。"""
    disable_proxy_env()
    cached = _bars_from_cache(code)
    if cached and len(cached) >= int(limit):
        return cached
    symbol = market_symbol(code)
    lmt = max(int(limit), 250)
    start = (date.today() - timedelta(days=500)).strftime("%Y-%m-%d")
    end = date.today().strftime("%Y-%m-%d")
    attempts = (
        (
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            {"param": f"{symbol},day,,,{lmt},qfq"},
        ),
        (
            "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
            {"_var": "kline_dayqfq", "param": f"{symbol},day,{start},{end},{lmt},qfq", "r": "0.1"},
        ),
    )
    last_exc: Exception | None = None
    for url, params in attempts:
        try:
            resp = _tx_get(url, params, timeout)
            if getattr(resp, "status_code", 0) != 200:
                raise RuntimeError(f"{url} HTTP {resp.status_code}")
            bars = _parse_tx_payload(resp.text, symbol)
            if bars:
                _store_cache(code, bars)
                return bars
            raise RuntimeError(f"{url} 无 K 线")
        except Exception as exc:
            last_exc = exc
            log.debug("K线 %s 腾讯 %s 失败：%s", code, url, exc)
    if last_exc:
        raise last_exc
    return []


def fetch_sina_bars(code: str, *, limit: int = 250, timeout: float = 12.0) -> list[dict[str, Any]]:
    disable_proxy_env()
    symbol = market_symbol(code)
    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    params = {
        "symbol": symbol,
        "scale": "240",
        "ma": "no",
        "datalen": str(max(int(limit), 250)),
    }
    resp = _tx_get(url, params, timeout)
    if getattr(resp, "status_code", 0) != 200:
        raise RuntimeError(f"sina kline HTTP {resp.status_code}")
    raw = resp.json()
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("sina kline 空表")
    bars: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        close = to_float(item.get("close"))
        if close is None:
            continue
        bars.append(
            _row(
                str(item.get("day") or ""),
                to_float(item.get("open")) or close,
                close,
                to_float(item.get("high")) or close,
                to_float(item.get("low")) or close,
                to_float(item.get("volume")) or 0.0,
            )
        )
    if not bars:
        raise RuntimeError("sina kline 无收盘价")
    _store_cache(code, bars)
    return bars


def _part(parts: list[str], idx: int, fallback: float) -> float:
    """取 klines 逗号串的第 idx 段，缺段或非数用 fallback。"""
    if len(parts) <= idx:
        return fallback
    return to_float(parts[idx]) or fallback


def _parse_em_bars(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") or {}
    klines = data.get("klines") or []
    bars: list[dict[str, Any]] = []
    for item in klines:
        parts = str(item).split(",")
        if len(parts) < 3:
            continue
        close = to_float(parts[2])
        if close is None:
            continue
        bars.append(
            _row(
                parts[0],
                _part(parts, 1, close),
                close,
                _part(parts, 3, close),
                _part(parts, 4, close),
                _part(parts, 5, 0.0),
            )
        )
    return bars


def _kline_params(code: str, limit: int) -> dict[str, str]:
    return {
        "secid": secid(code),
        "ut": UT,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "1",
        "end": "20500101",
        "lmt": str(max(int(limit), 250)),
    }


def fetch_eastmoney_bars(code: str, *, limit: int = 250, timeout: float = 12.0) -> list[dict[str, Any]]:
    """东财 his 最后手段。集群熔断后不再打。"""
    disable_proxy_env()
    if KLINE_BREAKER.is_open:
        raise RuntimeError("东财 K 线集群本轮已熔断")
    params = _kline_params(code, limit)

    def attempt(host: str):
        url = host.rstrip("/") + KLINE_PATH
        resp = _impersonated_get(url, params, timeout)
        if getattr(resp, "status_code", 0) != 200:
            raise RuntimeError(f"{host} HTTP {resp.status_code}")
        bars = _parse_em_bars(resp.json())
        if bars:
            return bars
        raise RuntimeError(f"{host} 无 K 线")

    def debug_log(host: str, exc: BaseException) -> None:
        log.debug("K线 %s %s 失败：%s", code, host, exc)

    bars, _host, last_exc, _tried = try_hosts(KLINE_BREAKER, attempt, debug_log=debug_log)
    if bars:
        _store_cache(code, bars)
        return bars
    raise last_exc or RuntimeError("东财 K 线全部失败")


def fetch_qfq_bars(
    code: str,
    *,
    limit: int = 250,
    timeout: float = 12.0,
) -> list[dict[str, Any]]:
    """按持久化顺序拉前复权日线。默认腾讯。"""
    disable_proxy_env()
    cached = _bars_from_cache(code)
    if cached and len(cached) >= int(limit):
        return cached

    loaders = {
        "tencent": fetch_tencent_bars,
        "eastmoney": fetch_eastmoney_bars,
    }
    failed: str | None = None
    last_exc: Exception | None = None
    for src in source_order("kline"):
        if KLINE_SOURCE_BREAKER.is_dead(src):
            continue
        loader = loaders.get(src)
        if loader is None:
            continue
        try:
            bars = loader(code, limit=limit, timeout=timeout)
            if bars:
                if failed:
                    promote_backup("kline", failed, src)
                return bars
            raise RuntimeError(f"{src} 无 K 线")
        except Exception as exc:
            last_exc = exc
            if failed is None:
                failed = src
            quiet = f"K线源 {src} 本轮不可达，改用后续备用源"
            KLINE_SOURCE_BREAKER.mark_dead(src, log, quiet)
            log.debug("K线 %s %s 失败：%s", code, src, exc)
    log.warning("K线全部失败 %s：%s", code, last_exc)
    return []


def fetch_qfq_closes(
    code: str,
    *,
    limit: int = 160,
    timeout: float = 12.0,
) -> list[float]:
    return _closes(fetch_qfq_bars(code, limit=limit, timeout=timeout))


def bars_to_frame(bars: list[dict[str, Any]]):
    import pandas as pd

    if not bars:
        return pd.DataFrame()
    out = pd.DataFrame(bars)
    out["date"] = pd.to_datetime(out["date"])
    for col in ("open", "close", "high", "low", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["date", "close"]).sort_values("date")
    return out.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)


def ma_last(closes: list[float], period: int = 120) -> tuple[float | None, float | None, int]:
    """返回 (最新收盘, MA{period}, 可用根数)。不足 period 根则均线为 None。"""
    n = len(closes)
    if n < 1:
        return None, None, 0
    close = closes[-1]
    if n < int(period):
        return close, None, n
    window = closes[-int(period) :]
    return close, sum(window) / float(period), n


def fetch_close_and_ma120(
    code: str,
    *,
    period: int = 120,
    bar_limit: int = 160,
    timeout: float = 12.0,
) -> tuple[float | None, float | None, int]:
    closes = fetch_qfq_closes(code, limit=bar_limit, timeout=timeout)
    return ma_last(closes, period=period)
