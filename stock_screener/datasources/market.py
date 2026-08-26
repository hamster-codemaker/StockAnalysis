"""akshare 公开数据封装：全A行情快照、业绩报表、机构持仓。"""

from __future__ import annotations

import logging
import time
from datetime import date

import akshare as ak
import pandas as pd
import requests

from ..downloader import USER_AGENT

log = logging.getLogger(__name__)

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _retry(func, label: str, tries: int = 3, wait: float = 5.0):
    for attempt in range(1, tries + 1):
        try:
            return func()
        except Exception as exc:
            log.warning("%s 获取失败（第%d次）：%s", label, attempt, exc)
            if attempt < tries:
                time.sleep(wait)
    return None


def reset_spot_breaker() -> None:
    breaker = getattr(get_spot, "_breaker", None)
    if breaker is not None:
        breaker.reset()


def _spot_from_clist() -> pd.DataFrame | None:
    """curl_cffi + push2delay 优先的实时 clist（与点金术快照同一客户端）。"""
    from dianjin.em_clist import ClistError, SPOT_FIELDS, fetch_clist_all

    try:
        rows = fetch_clist_all(
            SPOT_FIELDS,
            page_size=100,
            sleep_seconds=0.12,
            timeout=12.0,
            min_rows=1000,
            log_prefix="全A实时行情",
        )
    except ClistError as exc:
        log.debug("clist 实时行情失败：%s", exc)
        return None
    if not rows:
        return None
    raw = pd.DataFrame(rows)
    df = pd.DataFrame(
        {
            "代码": raw["f12"].astype(str).str.zfill(6),
            "名称": raw["f14"],
            "最新价": pd.to_numeric(raw["f2"], errors="coerce"),
            "总市值": pd.to_numeric(raw["f20"], errors="coerce"),
            "流通市值": pd.to_numeric(raw["f21"], errors="coerce"),
        }
    )
    return df.drop_duplicates(subset=["代码"], keep="last")


def _spot_from_tencent() -> pd.DataFrame | None:
    """腾讯全 A 排行：代码/名称/最新价/总市值/流通市值。无三档 PE、无股息率。"""
    from dianjin.em_cluster import CHROME_UA
    from dianjin.http_public import public_get

    url = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"
    page_size = 200
    headers = {"User-Agent": CHROME_UA, "Referer": "https://stockapp.finance.qq.com/"}
    params = {
        "_appver": "11.17.0",
        "board_code": "aStock",
        "sort_type": "price",
        "direct": "down",
        "offset": "0",
        "count": str(page_size),
    }

    def pull(offset: int) -> dict:
        resp = public_get(
            url,
            params={**params, "offset": str(offset)},
            headers=headers,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    first = pull(0)
    data = first.get("data") or {}
    rows = list(data.get("rank_list") or [])
    total = int(data.get("total") or 0)
    offset = page_size
    while offset < total and offset <= 8000:
        page = pull(offset)
        chunk = ((page.get("data") or {}).get("rank_list")) or []
        if not chunk:
            break
        rows.extend(chunk)
        offset += page_size
        time.sleep(0.12)
    if len(rows) < 1000:
        return None
    raw = pd.DataFrame(rows)
    codes = raw["code"].astype(str).str.replace(r"^(sh|sz|bj)", "", regex=True).str.zfill(6)
    zsz = pd.to_numeric(raw["zsz"], errors="coerce") * 1e8
    ltsz = pd.to_numeric(raw["ltsz"], errors="coerce") * 1e8
    df = pd.DataFrame(
        {
            "代码": codes,
            "名称": raw["name"],
            "最新价": pd.to_numeric(raw["zxj"], errors="coerce"),
            "总市值": zsz,
            "流通市值": ltsz,
        }
    )
    return df.drop_duplicates(subset=["代码"], keep="last")


def get_spot() -> pd.DataFrame | None:
    """全A行情快照，含名称、最新价、总市值、流通市值。

    默认腾讯排行优先；数据中心估值其次。不再使用东财 clist / push2。
    腾讯排行没有点金术所需的完整三档 PE；点金术快照走数据中心 + 腾讯行情下标 52。
    """
    from dianjin.source_pref import SourceBreaker, promote_backup, source_order

    loaders = {
        "tencent": _spot_from_tencent,
        "datacenter": _spot_from_datacenter,
        "clist": _spot_from_clist,
    }
    labels = {
        "tencent": "腾讯全A排行",
        "datacenter": "估值快照(数据中心)",
        "clist": "东财 clist",
    }
    breaker = getattr(get_spot, "_breaker", None)
    if breaker is None:
        breaker = SourceBreaker("spot")
        get_spot._breaker = breaker  # type: ignore[attr-defined]
    failed: str | None = None
    for src in source_order("spot"):
        if breaker.is_dead(src):
            continue
        loader = loaders.get(src)
        if loader is None:
            continue
        try:
            df = loader()
            if df is not None and not df.empty:
                if failed:
                    promote_backup("spot", failed, src)
                if src != "tencent":
                    log.info("全A行情已用%s，共 %d 只", labels[src], len(df))
                return df
            raise RuntimeError("空表")
        except Exception as exc:
            if failed is None:
                failed = src
            breaker.mark_dead(src, log, f"{labels[src]}本轮不可达，改用后续备用源")
            log.debug("%s 失败：%s", labels[src], exc)
    return None


def _spot_from_datacenter() -> pd.DataFrame | None:
    """备用行情源：东财数据中心 RPT_VALUEANALYSIS_DET，全市场最近收盘日估值。"""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    base = {
        "reportName": "RPT_VALUEANALYSIS_DET",
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
    }

    def fetch(params: dict) -> dict:
        resp = session.get(DATACENTER_URL, params={**base, **params}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    probe = fetch({
        "pageSize": "1",
        "pageNumber": "1",
        "sortColumns": "TRADE_DATE",
        "sortTypes": "-1",
        "filter": '(SECURITY_CODE="000001")',
    })
    probe_rows = (probe.get("result") or {}).get("data") or []
    if not probe_rows:
        return None
    trade_date = str(probe_rows[0]["TRADE_DATE"])[:10]

    all_rows: list[dict] = []
    page = 1
    while page <= 10:
        data = fetch({
            "pageSize": "5000",
            "pageNumber": str(page),
            "sortColumns": "SECURITY_CODE",
            "sortTypes": "1",
            "filter": f"(TRADE_DATE='{trade_date}')",
        })
        result = data.get("result") or {}
        all_rows.extend(result.get("data") or [])
        if page >= int(result.get("pages") or 1):
            break
        page += 1
        time.sleep(1.0)
    if not all_rows:
        return None
    raw = pd.DataFrame(all_rows)
    df = pd.DataFrame({
        "代码": raw["SECURITY_CODE"].astype(str).str.zfill(6),
        "名称": raw["SECURITY_NAME_ABBR"],
        "最新价": pd.to_numeric(raw["CLOSE_PRICE"], errors="coerce"),
        "总市值": pd.to_numeric(raw["TOTAL_MARKET_CAP"], errors="coerce"),
        "流通市值": pd.to_numeric(raw["NOTLIMITED_MARKETCAP_A"], errors="coerce"),
    })
    log.info("估值快照（%s）共 %d 只", trade_date, len(df))
    return df


def recent_report_periods(n: int = 6) -> list[str]:
    """从今天往前的 n 个已结束报告期期末日（YYYYMMDD），由新到旧。"""
    today = date.today()
    periods: list[date] = []
    for year in range(today.year, today.year - (n // 4 + 3), -1):
        for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
            p = date(year, month, day)
            if p <= today:
                periods.append(p)
    return [p.strftime("%Y%m%d") for p in sorted(periods, reverse=True)[:n]]


def recent_quarters(n: int = 2) -> list[str]:
    """新浪机构持股接口的季度参数（如 20261 = 2026年一季度），由新到旧取 n 个。"""
    result = []
    for period in recent_report_periods(n):
        year, month = int(period[:4]), int(period[4:6])
        result.append(f"{year}{(month + 2) // 3}")
    return result


def get_performance(period: str) -> pd.DataFrame | None:
    """东方财富业绩报表（含营收/净利同比增速、所处行业）。period 形如 20260630。"""
    df = _retry(lambda: ak.stock_yjbb_em(date=period), f"业绩报表 {period}")
    if df is None or df.empty:
        return None
    df = df.copy()
    df["股票代码"] = df["股票代码"].astype(str).str.zfill(6)
    return df


def get_institute_hold(quarter: str) -> pd.DataFrame | None:
    """新浪机构持股（机构数、占流通股比例）。quarter 形如 20261。"""
    df = _retry(lambda: ak.stock_institute_hold(quarter), f"机构持股 {quarter}", tries=2, wait=3.0)
    if df is None or df.empty:
        return None
    df = df.copy()
    code_col = next((c for c in df.columns if "代码" in str(c)), df.columns[0])
    df[code_col] = df[code_col].astype(str).str.zfill(6)
    return df


def get_fund_hold(period: str) -> pd.DataFrame | None:
    """东方财富基金持仓。period 形如 20260630。"""
    df = _retry(
        lambda: ak.stock_report_fund_hold(symbol="基金持仓", date=period),
        f"基金持仓 {period}",
        tries=2,
        wait=3.0,
    )
    if df is None or df.empty:
        return None
    df = df.copy()
    df["股票代码"] = df["股票代码"].astype(str).str.zfill(6)
    return df
