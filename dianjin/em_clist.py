"""东财 clist 分页：curl_cffi + push2delay 优先，进程级熔断。

与 akshare stock_zh_a_spot_em 同一套 fs；本机 82.push2 常被断开，
粘滞可用主机（常见为 push2delay），不再逐页轮询死主机。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from dianjin.em_cluster import (
    CHROME_UA,
    ClusterBreaker,
    disable_proxy_env,
    impersonated_get,
    try_hosts,
)
from dianjin.kline import secid

log = logging.getLogger("dianjin")

CLIST_PATH = "/api/qt/clist/get"
CLIST_HOSTS = (
    "https://push2delay.eastmoney.com",
    "https://82.push2.eastmoney.com",
    "https://88.push2.eastmoney.com",
    "https://push2.eastmoney.com",
    "https://7.push2.eastmoney.com",
    "https://28.push2.eastmoney.com",
)
A_SHARE_FS = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"
UT = "bd1d9ddb04089700cf9c27f6f7426281"
HEADERS = {
    "User-Agent": CHROME_UA,
    "Referer": "https://quote.eastmoney.com/center/gridlist.html",
}
# f133=同花顺「股息率TTM」同列。不要用 f183，那不是股息率。
VALUATION_FIELDS = "f2,f9,f12,f14,f114,f115,f133"
SPOT_FIELDS = "f2,f12,f14,f20,f21"

CLIST_BREAKER = ClusterBreaker(
    "push2-clist",
    CLIST_HOSTS,
    trip_after=2,
    hard_fail_host_cap=2,
    announce_message="东财实时行情集群不可达，本轮不再轮询 push2",
)


def _load_sticky_host() -> None:
    try:
        from dianjin.source_pref import clist_preferred_host

        host = clist_preferred_host()
        if host and host in CLIST_HOSTS:
            CLIST_BREAKER.last_good = host
    except Exception:
        pass


_load_sticky_host()


class ClistError(RuntimeError):
    """clist 集群失败或收录过少。调用方不得因此放宽市盈率规则。"""


def reset_clist_breaker() -> None:
    CLIST_BREAKER.reset()
    _load_sticky_host()


def parse_diff(payload: dict[str, Any]) -> list[dict]:
    data = payload.get("data") or {}
    diff = data.get("diff")
    if not diff:
        return []
    if isinstance(diff, dict):
        return [v for v in diff.values() if isinstance(v, dict)]
    if isinstance(diff, list):
        return [v for v in diff if isinstance(v, dict)]
    return []


def _page_params(pn: int, pz: int, fields: str) -> dict[str, str]:
    return {
        "pn": str(pn),
        "pz": str(pz),
        "po": "1",
        "np": "1",
        "ut": UT,
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": A_SHARE_FS,
        "fields": fields,
    }


def fetch_clist_page(
    pn: int,
    pz: int,
    fields: str,
    *,
    timeout: float = 12.0,
    log_prefix: str = "clist",
) -> tuple[list[dict], int]:
    """拉一页 clist。熔断后立即失败，硬失败最多试 2 个主机。"""
    disable_proxy_env()
    if CLIST_BREAKER.is_open:
        raise ClistError(f"{log_prefix} 第 {pn} 页跳过：集群本轮已熔断")
    params = _page_params(pn, pz, fields)

    def attempt(host: str):
        url = host.rstrip("/") + CLIST_PATH
        resp = impersonated_get(url, params, timeout, HEADERS)
        if getattr(resp, "status_code", 0) != 200:
            raise RuntimeError(f"{host} HTTP {resp.status_code}")
        payload = resp.json()
        rows = parse_diff(payload)
        total = int((payload.get("data") or {}).get("total") or 0)
        if rows or total == 0:
            return (rows, total)
        raise RuntimeError(f"{host} rc={payload.get('rc')} empty page pn={pn}")

    def debug_log(host: str, exc: BaseException) -> None:
        log.debug("%s pn=%d %s 失败：%s", log_prefix, pn, host, exc)

    prev = CLIST_BREAKER.last_good
    result, host, last_exc, _tried = try_hosts(CLIST_BREAKER, attempt, debug_log=debug_log)
    if result:
        rows, total = result
        if host and host != prev:
            if prev:
                log.info("%s改用主机 %s", log_prefix, host)
            try:
                from dianjin.source_pref import save_clist_preferred_host

                save_clist_preferred_host(host)
            except Exception:
                pass
        return rows, total
    raise ClistError(f"{log_prefix} 第 {pn} 页全部主机失败：{last_exc}")


def fetch_clist_all(
    fields: str,
    *,
    page_size: int = 100,
    sleep_seconds: float = 0.15,
    timeout: float = 12.0,
    min_rows: int = 1000,
    log_prefix: str = "估值快照",
) -> list[dict]:
    """分页拉全 A clist。失败抛 ClistError。"""
    disable_proxy_env()
    if CLIST_BREAKER.is_open:
        raise ClistError(f"{log_prefix}跳过：东财实时行情集群本轮已熔断")
    pz = max(1, min(int(page_size), 100))
    first_rows, total = fetch_clist_page(1, pz, fields, timeout=timeout, log_prefix=log_prefix)
    if total <= 0 and not first_rows:
        raise ClistError(f"{log_prefix}返回空市（total=0）")
    pages = max(1, (int(total) + pz - 1) // pz) if total else 1
    log.info(
        "%s：声明总数 %d，页大小 %d，约 %d 页（主机 %s）",
        log_prefix,
        total,
        pz,
        pages,
        CLIST_BREAKER.last_good,
    )

    collected: dict[str, dict] = {}
    for raw in first_rows:
        code = str(raw.get("f12") or "").strip().zfill(6)
        if code.isdigit() and len(code) == 6:
            collected[code] = raw

    for pn in range(2, pages + 1):
        time.sleep(max(0.05, float(sleep_seconds)))
        rows, _ = fetch_clist_page(pn, pz, fields, timeout=timeout, log_prefix=log_prefix)
        if not rows:
            log.warning("%s第 %d/%d 页为空，停止翻页", log_prefix, pn, pages)
            break
        for raw in rows:
            code = str(raw.get("f12") or "").strip().zfill(6)
            if code.isdigit() and len(code) == 6:
                collected[code] = raw
        if pn % 10 == 0 or pn == pages:
            log.info("%s进度 %d/%d 页，已收录 %d 只", log_prefix, pn, pages, len(collected))

    if len(collected) < int(min_rows):
        raise ClistError(f"{log_prefix}收录过少（{len(collected)}）")
    log.info("%s完成：%d 只", log_prefix, len(collected))
    return list(collected.values())


def fetch_clist_by_codes(
    codes: list[str],
    fields: str,
    *,
    timeout: float = 12.0,
    batch_size: int = 80,
    log_prefix: str = "clist补全",
) -> list[dict]:
    """按代码批量拉 clist（二次确认缺 PE/股息时用）。"""
    disable_proxy_env()
    wanted = [str(c).zfill(6) for c in codes if str(c).strip()]
    if not wanted or CLIST_BREAKER.is_open:
        return []
    collected: dict[str, dict] = {}
    step = max(1, min(int(batch_size), 80))
    for i in range(0, len(wanted), step):
        chunk = wanted[i : i + step]
        fs = ",".join(f"i:{secid(code)}" for code in chunk)
        params = {
            "pn": "1",
            "pz": str(len(chunk)),
            "po": "1",
            "np": "1",
            "ut": UT,
            "fltt": "2",
            "invt": "2",
            "fid": "f12",
            "fs": fs,
            "fields": fields,
        }

        def attempt(host: str, _params=params):
            url = host.rstrip("/") + CLIST_PATH
            resp = impersonated_get(url, _params, timeout, HEADERS)
            if getattr(resp, "status_code", 0) != 200:
                raise RuntimeError(f"{host} HTTP {resp.status_code}")
            rows = parse_diff(resp.json())
            if rows:
                return rows
            raise RuntimeError(f"{host} 无数据")

        def debug_log(host: str, exc: BaseException) -> None:
            log.debug("%s %s 失败：%s", log_prefix, host, exc)

        rows, _host, _exc, _tried = try_hosts(CLIST_BREAKER, attempt, debug_log=debug_log)
        if not rows:
            continue
        for raw in rows:
            code = str(raw.get("f12") or "").strip().zfill(6)
            if code.isdigit() and len(code) == 6:
                collected[code] = raw
    log.info("%s：请求 %d 只，收回 %d 只", log_prefix, len(wanted), len(collected))
    return list(collected.values())
