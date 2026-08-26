"""点金术筛选用股息率 = 同花顺「股息率TTM」（百分数）。

用户锚定（2026-08-18）：
- 同花顺股息率TTM：思维列控 603508 = 2.43，中油资本 000617 ≈ 1.50。
- 东财 clist **f133** 与上述同列（全市场快照用它，避免五千页 F10）。
- 独立按同花顺 F10 分红表计算见 dianjin.ths_yield。

不是官方、不得再当作股息率TTM：
- 东财 **f183**（中油资本 3.03）是资金流占比，不是股息率。
- 同花顺 realhead **526792** 与腾讯下标 **43** 是振幅（中油资本 2.32）。
- 腾讯下标 **64** 常是年度/含特别分红（思维列控 12.18），不是股息率TTM。
  中油资本碰巧也是 1.50，不能当全市场对照。

本模块数值已经是百分数（1.50 表示 1.50%），不要再 /100 或 ×100。
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Any

from dianjin.em_cluster import CHROME_UA, disable_proxy_env
from dianjin.rules import to_float

log = logging.getLogger("dianjin")

# 东财 clist：与同花顺「股息率TTM」同列（单位 %）。筛选用此字段。
EM_SCREEN_YIELD_FIELD = "f133"
# 东财 clist：不是股息率。禁止用于筛选或展示为股息率。
EM_NOT_YIELD_FIELD = "f183"
# 腾讯个股行情（~ 分割，0 起算）
# 39=市盈率TTM（锚点 000617 = 18.69 = f115），不是动态 PE
# 52=动态市盈率（000617 = 13.64 = f9）
# 53=静态市盈率（000617 = 19.96 = f114）
# 43=振幅%；64=年度/含特别分红口径，不是股息率TTM
TX_PE_TTM_INDEX = 39
TX_AMPLITUDE_INDEX = 43
TX_PE_DYN_INDEX = 52
TX_PE_STATIC_INDEX = 53
TX_YIELD_INDEX = 64
# 同花顺 realhead：振幅，不是选股股息率
THS_AMPLITUDE_FIELD = "526792"

_TX_HEADERS = {
    "User-Agent": CHROME_UA,
    "Referer": "https://gu.qq.com/",
}


def parse_yield_percent(value: Any) -> float | None:
    """行情里已经是百分数。1.50 → 1.50；禁止把 1.50 当成比率再 ×100。"""
    return to_float(value)


def screen_yield_from_clist(raw: dict[str, Any] | None) -> float | None:
    """只读 f133。缺数返回 None，绝不改读 f183。"""
    if not raw:
        return None
    return parse_yield_percent(raw.get(EM_SCREEN_YIELD_FIELD))


def not_a_yield_f183(raw: dict[str, Any] | None) -> float | None:
    """f183 原值，仅供对照测试证明它不是股息率。"""
    if not raw:
        return None
    return to_float(raw.get(EM_NOT_YIELD_FIELD))


def dps_from_10_share_payout(pretax_bonus_rmb: Any) -> float | None:
    """东财 PRETAX_BONUS_RMB 是「10 派 X 元」里的 X，每股现金 = X/10。

    把 0.47 当成每股（不再 /10）会把股息率放大 10 倍。
    """
    cash_per_10 = to_float(pretax_bonus_rmb)
    if cash_per_10 is None:
        return None
    return cash_per_10 / 10.0


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    compact = text[:10].replace("/", "-")
    try:
        return datetime.strptime(compact, "%Y-%m-%d").date()
    except ValueError:
        digits = "".join(ch for ch in text if ch.isdigit())[:8]
        if len(digits) == 8:
            try:
                return datetime.strptime(digits, "%Y%m%d").date()
            except ValueError:
                return None
        return None


def ttm_cash_dps(
    payouts: list[dict[str, Any]],
    *,
    as_of: date,
    window_days: int = 365,
) -> float | None:
    """近 window_days 内、已实施分配的每股现金分红合计。

    每条 payout 可用：ex_date / EX_DIVIDEND_DATE、pretax_bonus_rmb / PRETAX_BONUS_RMB、
    status / ASSIGN_PROGRESS（缺省视为已实施）。
    """
    start = as_of - timedelta(days=int(window_days))
    total = 0.0
    saw = False
    for row in payouts:
        status = str(row.get("status") or row.get("ASSIGN_PROGRESS") or "实施分配")
        if status and "实施" not in status:
            continue
        ex_div = _parse_date(row.get("ex_date") or row.get("EX_DIVIDEND_DATE"))
        if ex_div is None or ex_div <= start or ex_div > as_of:
            continue
        dps = dps_from_10_share_payout(row.get("pretax_bonus_rmb") or row.get("PRETAX_BONUS_RMB"))
        if dps is None:
            continue
        total += dps
        saw = True
    return total if saw else None


def yield_from_dps(dps: Any, price: Any) -> float | None:
    cash = to_float(dps)
    px = to_float(price)
    if cash is None or px is None or px <= 0:
        return None
    return cash / px * 100.0


def ttm_yield_percent(
    payouts: list[dict[str, Any]],
    price: Any,
    *,
    as_of: date,
    window_days: int = 365,
) -> float | None:
    return yield_from_dps(ttm_cash_dps(payouts, as_of=as_of, window_days=window_days), price)


def parse_tencent_fields(text: str) -> list[str] | None:
    blob = str(text or "").strip()
    if "~\"" in blob or blob.startswith("v_"):
        match = re.search(r'="(.*)"\s*;?\s*$', blob, re.S)
        if match:
            blob = match.group(1)
    if "~" not in blob:
        return None
    return blob.split("~")


def parse_tencent_quotes(text: str) -> dict[str, list[str]]:
    """解析 qt.gtimg.cn 批量返回：code → 字段列表。"""
    out: dict[str, list[str]] = {}
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or "~" not in line:
            continue
        fields = parse_tencent_fields(line)
        if not fields or len(fields) < 3:
            continue
        code = str(fields[2]).zfill(6)
        if code.isdigit():
            out[code] = fields
    return out


def yield_from_tencent_fields(fields: list[str] | None) -> float | None:
    if not fields or len(fields) <= TX_YIELD_INDEX:
        return None
    return parse_yield_percent(fields[TX_YIELD_INDEX])


def pe_dyn_from_tencent_fields(fields: list[str] | None) -> float | None:
    """动态市盈率，对应原东财 clist f9。不要用下标 39（那是 TTM）。"""
    if not fields or len(fields) <= TX_PE_DYN_INDEX:
        return None
    return to_float(fields[TX_PE_DYN_INDEX])


def amplitude_from_tencent_fields(fields: list[str] | None) -> float | None:
    if not fields or len(fields) <= TX_AMPLITUDE_INDEX:
        return None
    return to_float(fields[TX_AMPLITUDE_INDEX])


def parse_realhead_items(text: str) -> dict[str, Any]:
    blob = str(text or "").strip()
    match = re.search(r"\(({.*})\)\s*$", blob, re.S)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    items = payload.get("items")
    return items if isinstance(items, dict) else {}


def amplitude_from_ths_items(items: dict[str, Any] | None) -> float | None:
    """526792 是振幅，不是股息率。"""
    if not items:
        return None
    return to_float(items.get(THS_AMPLITUDE_FIELD))


def tencent_symbol(code: str) -> str:
    from dianjin.kline import market_symbol

    return market_symbol(code)


def fetch_tencent_quotes(
    codes: list[str],
    *,
    batch_size: int = 80,
    sleep_seconds: float = 0.12,
    timeout: float = 12.0,
) -> dict[str, list[str]]:
    """批量拉腾讯个股行情。失败的代码不出现在结果里，不回退东财 f183。"""
    disable_proxy_env()
    wanted = [str(c).zfill(6) for c in codes if str(c).strip()]
    collected: dict[str, list[str]] = {}
    step = max(1, min(int(batch_size), 80))
    from dianjin.http_public import public_get

    for i in range(0, len(wanted), step):
        if i:
            time.sleep(max(0.0, float(sleep_seconds)))
        chunk = wanted[i : i + step]
        query = ",".join(tencent_symbol(code) for code in chunk)
        url = f"https://qt.gtimg.cn/q={query}"
        try:
            resp = public_get(url, timeout=timeout, headers=_TX_HEADERS)
            if getattr(resp, "status_code", 0) != 200:
                log.debug("腾讯行情 HTTP %s batch=%d", resp.status_code, i)
                continue
            collected.update(parse_tencent_quotes(resp.text))
        except Exception as exc:
            log.debug("腾讯行情 batch=%d 失败：%s", i, exc)
            continue
    return collected
