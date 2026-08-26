"""同花顺「股息率TTM」：从 F10 分红表计算，并证明 526792 是振幅。

realhead 默认字段里没有股息率TTM（3475914=总市值，526792=振幅）。
问财/自定义列需要 hexin-v，本机常被拦截。因此直接用同花顺
https://basic.10jqka.com.cn/{code}/bonus.html 的已实施方案，按用户锚定口径：

- 思维列控 603508 = 2.43（剔除 2025 中报 10 派 21、股利支付率 262% 的特别分红）
- 中油资本 000617 ≈ 1.50（近 12 个月 10 派 0.47+0.55）

点金术全市场快照预填东财数据中心 DV_TTM（与原 clist f133 同列）；
过市盈率关后再用本模块 F10 分红表覆盖。不用 f183，也不用腾讯[64]。
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, timedelta
from io import StringIO
from typing import Any

from dianjin.em_cluster import disable_proxy_env
from dianjin.rules import to_float
from dianjin.yield_quote import (
    amplitude_from_ths_items,
    dps_from_10_share_payout,
    parse_realhead_items,
    parse_yield_percent,
    yield_from_dps,
)

log = logging.getLogger("dianjin")

# 股利支付率超过该值视为特别分红，同花顺 TTM 不计入（思维列控 262%）
THS_SPECIAL_PAYOUT_RATIO = 200.0
# 已实施方案、除权日尚未到（常见为公告后数日）仍计入
THS_FUTURE_EX_DAYS = 21
# 近 12 个月加两周宽限：泸州老窖 2024 年报除权 2025-08-08 仍落在 2026-08-18 的 TTM 内
THS_WINDOW_DAYS = 380
THS_BONUS_URL = "https://basic.10jqka.com.cn/new/{code}/bonus.html"

_PLAN_CASH = re.compile(r"派\s*([0-9.]+)\s*元")

__all__ = [
    "THS_SPECIAL_PAYOUT_RATIO",
    "parse_realhead_items",
    "amplitude_from_ths_items",
    "parse_bonus_plan_dps",
    "parse_ths_bonus_html",
    "ths_ttm_cash_dps",
    "ths_ttm_yield_percent",
    "fetch_ths_bonus_payouts",
    "fetch_ths_ttm_map",
    "overlay_ths_ttm",
]


def parse_bonus_plan_dps(plan: Any) -> float | None:
    """「10派0.47元(含税)」→ 每股 0.047。无派现返回 None。"""
    text = str(plan or "")
    match = _PLAN_CASH.search(text)
    if not match:
        return None
    return dps_from_10_share_payout(match.group(1))


def _as_date(value: Any) -> date | None:
    from datetime import datetime

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime().date()
        except Exception:
            pass
    text = str(value).strip()
    if not text or text in {"--", "-", "nan", "NaT", "None"}:
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


def _payout_ratio(value: Any) -> float | None:
    text = str(value or "").replace("%", "").strip()
    return to_float(text)


def parse_ths_bonus_html(html: str) -> list[dict[str, Any]]:
    """解析同花顺 bonus.html 第一张分红表。"""
    import pandas as pd

    text = str(html or "")
    if not text.strip():
        return []
    try:
        tables = pd.read_html(StringIO(text))
    except ValueError:
        return []
    if not tables:
        return []
    frame = tables[0]
    rows: list[dict[str, Any]] = []
    for rec in frame.to_dict(orient="records"):
        plan = rec.get("分红方案说明")
        dps = parse_bonus_plan_dps(plan)
        rows.append(
            {
                "period": rec.get("报告期"),
                "status": rec.get("方案进度"),
                "ex_date": _as_date(rec.get("A股除权除息日")),
                "announce_date": _as_date(rec.get("实施公告日")),
                "plan": plan,
                "dps": dps,
                "payout_ratio": _payout_ratio(rec.get("股利支付率")),
                "pretax_bonus_rmb": (dps * 10.0) if dps is not None else None,
            }
        )
    return rows


def ths_ttm_cash_dps(
    payouts: list[dict[str, Any]],
    *,
    as_of: date,
    window_days: int = THS_WINDOW_DAYS,
    future_ex_days: int = THS_FUTURE_EX_DAYS,
    special_payout_ratio: float = THS_SPECIAL_PAYOUT_RATIO,
) -> float | None:
    """同花顺股息率TTM 的分子：近 window_days 已实施现金分红（含即将除权）。

    特判（只为对齐同花顺，不向腾讯年度口径靠）：
    - 方案进度须含「实施」；预案不计。
    - 股利支付率 > 200% 视为特别分红，剔除（思维列控 10 派 21）。
    - 除权日可晚于 as_of 最多 future_ex_days（已公告实施方案）。
    """
    start = as_of - timedelta(days=int(window_days))
    end = as_of + timedelta(days=int(future_ex_days))
    total = 0.0
    saw = False
    for row in payouts:
        status = str(row.get("status") or row.get("ASSIGN_PROGRESS") or "")
        if "实施" not in status:
            continue
        ratio = _payout_ratio(row.get("payout_ratio") or row.get("股利支付率"))
        if ratio is not None and ratio > float(special_payout_ratio):
            continue
        ex_div = _as_date(row.get("ex_date") or row.get("EX_DIVIDEND_DATE"))
        if ex_div is None:
            continue
        if ex_div <= start or ex_div > end:
            continue
        dps = to_float(row.get("dps"))
        if dps is None:
            dps = parse_bonus_plan_dps(row.get("plan") or row.get("分红方案说明"))
        if dps is None:
            dps = dps_from_10_share_payout(
                row.get("pretax_bonus_rmb") or row.get("PRETAX_BONUS_RMB")
            )
        if dps is None:
            continue
        total += dps
        saw = True
    return total if saw else None


def ths_ttm_yield_percent(
    payouts: list[dict[str, Any]],
    price: Any,
    *,
    as_of: date,
    window_days: int = THS_WINDOW_DAYS,
) -> float | None:
    return yield_from_dps(
        ths_ttm_cash_dps(payouts, as_of=as_of, window_days=window_days),
        price,
    )


def _decode_ths(resp) -> str:
    raw = getattr(resp, "content", b"") or b""
    for enc in ("gbk", "gb2312", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return getattr(resp, "text", "") or ""


def fetch_ths_bonus_payouts(
    code: str, *, timeout: float = 15.0
) -> tuple[list[dict[str, Any]], bool]:
    """拉取同花顺分红表。返回 (rows, ok)；ok=False 表示网络/解析失败，不是真的没分红。"""
    disable_proxy_env()
    symbol = str(code).zfill(6)
    url = THS_BONUS_URL.format(code=symbol)
    try:
        from dianjin.http_public import public_get

        resp = public_get(
            url,
            timeout=timeout,
            headers={"Referer": "https://basic.10jqka.com.cn/"},
        )
        if getattr(resp, "status_code", 0) != 200:
            return [], False
        rows = parse_ths_bonus_html(_decode_ths(resp))
        return rows, True
    except Exception as exc:
        log.debug("同花顺分红表 %s 失败：%s", symbol, exc)
        return [], False


def fetch_ths_ttm_map(
    rows: list[dict[str, Any]],
    *,
    as_of: date | None = None,
    sleep_seconds: float = 0.15,
    timeout: float = 15.0,
) -> dict[str, float | None]:
    """按收盘价计算同花顺股息率TTM。rows 需有 code、close。缺分红或缺价 → None。"""
    day = as_of or date.today()
    out: dict[str, float | None] = {}
    for i, row in enumerate(rows):
        code = str(row.get("code") or "").zfill(6)
        if not code.isdigit():
            continue
        if i:
            time.sleep(max(0.0, float(sleep_seconds)))
        payouts, ok = fetch_ths_bonus_payouts(code, timeout=timeout)
        if not ok:
            out[code] = None
            continue
        out[code] = parse_yield_percent(
            ths_ttm_yield_percent(payouts, row.get("close") or row.get("price"), as_of=day)
        )
        if (i + 1) % 25 == 0 or i + 1 == len(rows):
            log.info("同花顺股息率TTM 进度 %d/%d", i + 1, len(rows))
    return out


def overlay_ths_ttm(
    rows: list[dict[str, Any]],
    *,
    as_of: date | None = None,
    sleep_seconds: float = 0.15,
    timeout: float = 15.0,
) -> tuple[int, int]:
    """用同花顺 F10 分红表覆盖 dividend。成功算出则写入；拉页失败保留原值。

    返回 (覆盖数, 拉页失败数)。成功但窗口内无派现 → dividend=None（缺数不通过）。
    """
    day = as_of or date.today()
    filled = 0
    failed = 0
    for i, row in enumerate(rows):
        code = str(row.get("code") or "").zfill(6)
        if not code.isdigit():
            continue
        if i:
            time.sleep(max(0.0, float(sleep_seconds)))
        payouts, ok = fetch_ths_bonus_payouts(code, timeout=timeout)
        if not ok:
            failed += 1
            continue
        row["dividend"] = parse_yield_percent(
            ths_ttm_yield_percent(payouts, row.get("close"), as_of=day)
        )
        filled += 1
        if (i + 1) % 25 == 0 or i + 1 == len(rows):
            log.info("同花顺股息率TTM 覆盖进度 %d/%d", i + 1, len(rows))
    log.info("同花顺股息率TTM 覆盖 %d，拉页失败保留快照 %d", filled, failed)
    return filled, failed
