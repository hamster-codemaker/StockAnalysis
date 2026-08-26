"""东财数据中心公开估值表（datacenter-web），替代 push2 clist。

RPT_VALUEANALYSIS_DET 是 data.eastmoney.com/gzfx 同源接口，无需登录。
列映射（与点金术行结构一致，筛选规则仍读 pe_dyn / pe_static / pe_ttm / dividend）：

- CLOSE_PRICE → close
- PE_LAR → pe_static（静态 / LYR，对应原 clist f114）
- PE_TTM → pe_ttm（对应原 clist f115）
- DV_TTM → dividend（快照预填；过 PE 关后仍由同花顺 F10 分红表覆盖）
- 动态市盈率：表内 PE_DYNAMIC / WEST_PE（若有）；否则用腾讯行情下标 52 补齐
  （对应原 clist f9）。缺数保持 None，不放宽三档市盈率规则。不使用新浪。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from dianjin.http_public import public_get
from dianjin.rules import to_float
from dianjin.yield_quote import parse_yield_percent, pe_dyn_from_tencent_fields

log = logging.getLogger("dianjin")

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

PE_DYN_KEYS = ("PE_DYNAMIC", "PE_DYN", "PE_ROLL", "WEST_PE", "PE")
PE_STATIC_KEYS = ("PE_LAR", "PE_LYR", "PE_STATIC")
PE_TTM_KEYS = ("PE_TTM", "PETTM")
DIV_KEYS = ("DV_TTM", "DIVIDEND_RATIO_TTM", "DV_RATIO")

_CACHE: list[dict[str, Any]] | None = None


class DatacenterError(RuntimeError):
    """估值快照失败。调用方不得因此放宽市盈率规则。"""


def reset_valuation_cache() -> None:
    global _CACHE
    _CACHE = None


def _first_float(raw: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in raw:
            value = to_float(raw.get(key))
            if value is not None:
                return value
    return None


def normalize_datacenter_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    code = str(raw.get("SECURITY_CODE") or raw.get("f12") or "").strip().zfill(6)
    if not code.isdigit() or len(code) != 6:
        return None
    pe_ttm = _first_float(raw, PE_TTM_KEYS)
    pe_dyn = _first_float(raw, PE_DYN_KEYS)
    if pe_dyn is not None and pe_ttm is not None and pe_dyn == pe_ttm:
        # 仅有一列 PE 时不要把 TTM 同时当成动态与 TTM 双计；动态留给腾讯下标 52。
        if "PE_DYNAMIC" not in raw and "PE_DYN" not in raw and "WEST_PE" not in raw:
            pe_dyn = None
    return {
        "code": code,
        "name": str(raw.get("SECURITY_NAME_ABBR") or raw.get("f14") or "").strip(),
        "close": to_float(raw.get("CLOSE_PRICE") if "CLOSE_PRICE" in raw else raw.get("f2")),
        "pe_dyn": pe_dyn,
        "pe_static": _first_float(raw, PE_STATIC_KEYS) or to_float(raw.get("f114")),
        "pe_ttm": pe_ttm if pe_ttm is not None else to_float(raw.get("f115")),
        "dividend": parse_yield_percent(_first_float(raw, DIV_KEYS) if any(k in raw for k in DIV_KEYS) else raw.get("f133")),
    }


def _datacenter_get(params: dict[str, str], *, timeout: float) -> dict[str, Any]:
    resp = public_get(
        DATACENTER_URL,
        params=params,
        headers={"Referer": "https://data.eastmoney.com/gzfx/"},
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise DatacenterError(f"数据中心 HTTP {resp.status_code}")
    payload = resp.json()
    if not isinstance(payload, dict):
        raise DatacenterError("数据中心返回非 JSON 对象")
    return payload


def _fetch_raw_rows(*, timeout: float = 30.0, sleep_seconds: float = 0.3) -> list[dict[str, Any]]:
    base = {
        "reportName": "RPT_VALUEANALYSIS_DET",
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
    }
    probe = _datacenter_get(
        {
            **base,
            "pageSize": "1",
            "pageNumber": "1",
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "filter": '(SECURITY_CODE="000001")',
        },
        timeout=timeout,
    )
    probe_rows = ((probe.get("result") or {}).get("data")) or []
    if not probe_rows:
        raise DatacenterError("数据中心估值表无样本")
    trade_date = str(probe_rows[0].get("TRADE_DATE") or "")[:10]
    if not trade_date:
        raise DatacenterError("数据中心估值表无交易日")

    all_rows: list[dict[str, Any]] = []
    page = 1
    pages = 1
    while page <= pages and page <= 20:
        data = _datacenter_get(
            {
                **base,
                "pageSize": "5000",
                "pageNumber": str(page),
                "sortColumns": "SECURITY_CODE",
                "sortTypes": "1",
                "filter": f"(TRADE_DATE='{trade_date}')",
            },
            timeout=timeout,
        )
        result = data.get("result") or {}
        chunk = result.get("data") or []
        all_rows.extend(chunk)
        pages = max(1, int(result.get("pages") or 1))
        if page >= pages:
            break
        page += 1
        time.sleep(max(0.0, float(sleep_seconds)))
    if len(all_rows) < 1000:
        raise DatacenterError(f"数据中心收录过少（{len(all_rows)}）")
    log.info("估值快照（数据中心 %s）声明 %d 条", trade_date, len(all_rows))
    return all_rows


def _fetch_tencent_pe_dyn(
    codes: list[str],
    *,
    timeout: float = 12.0,
    sleep_seconds: float = 0.12,
) -> dict[str, float]:
    """腾讯行情下标 52 → 动态市盈率。失败的代码不出现，不放宽规则。"""
    from dianjin.yield_quote import fetch_tencent_quotes

    wanted = [str(c).zfill(6) for c in codes if str(c).strip()]
    quotes = fetch_tencent_quotes(
        wanted, batch_size=80, sleep_seconds=sleep_seconds, timeout=timeout
    )
    out: dict[str, float] = {}
    for code, fields in quotes.items():
        pe = pe_dyn_from_tencent_fields(fields)
        if pe is not None:
            out[code] = pe
    if out:
        log.info("腾讯动态市盈率：%d 只", len(out))
    return out


def overlay_pe_dyn(rows: list[dict[str, Any]], pe_map: dict[str, float] | None = None) -> int:
    """只填 pe_dyn 为 None 的行。已有动态市盈率不覆盖。"""
    if pe_map is None:
        missing = [
            str(row.get("code") or "").zfill(6)
            for row in rows
            if to_float(row.get("pe_dyn")) is None
        ]
        mapping = _fetch_tencent_pe_dyn(missing) if missing else {}
    else:
        mapping = pe_map
    filled = 0
    for row in rows:
        if to_float(row.get("pe_dyn")) is not None:
            continue
        extra = mapping.get(str(row.get("code") or "").zfill(6))
        if extra is None:
            continue
        row["pe_dyn"] = extra
        filled += 1
    if filled:
        log.info("动态市盈率补齐 %d 只（腾讯下标 52）", filled)
    return filled


def fetch_valuation_rows(
    *,
    timeout: float = 30.0,
    sleep_seconds: float = 0.3,
    use_cache: bool = True,
    fill_pe_dyn: bool = True,
) -> list[dict[str, Any]]:
    """全市场估值快照，结构与原 clist 归一化行相同。"""
    global _CACHE
    if use_cache and _CACHE:
        return _CACHE
    collected: dict[str, dict[str, Any]] = {}
    for raw in _fetch_raw_rows(timeout=timeout, sleep_seconds=sleep_seconds):
        row = normalize_datacenter_row(raw)
        if row:
            collected[row["code"]] = row
    rows = list(collected.values())
    if fill_pe_dyn:
        overlay_pe_dyn(rows)
    if len(rows) < 1000:
        raise DatacenterError(f"估值快照收录过少（{len(rows)}）")
    _CACHE = rows
    return rows
