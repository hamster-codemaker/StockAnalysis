"""全市场估值快照：东财数据中心公开表（替代 push2 clist）。

行结构不变：code / name / close / pe_dyn / pe_static / pe_ttm / dividend。
筛选规则仍是三档 PE + 同花顺股息率TTM 覆盖，不因换源改阈值。
"""

from __future__ import annotations

import logging
from typing import Any

from dianjin.em_datacenter import DatacenterError as SnapshotError
from dianjin.em_datacenter import fetch_valuation_rows
from dianjin.rules import to_float
from dianjin.yield_quote import screen_yield_from_clist

log = logging.getLogger("dianjin")

__all__ = [
    "SnapshotError",
    "fetch_market_snapshot",
    "valuation_incomplete",
    "valuation_gappy",
    "fill_missing_valuation",
    "_normalize_row",
]


def valuation_incomplete(row: dict[str, Any]) -> bool:
    return any(
        to_float(row.get(key)) is None
        for key in ("pe_dyn", "pe_static", "pe_ttm", "dividend")
    )


def valuation_gappy(row: dict[str, Any]) -> bool:
    """像主机漏字段，而不是亏损股本来就没有 PE。"""
    if to_float(row.get("close")) is None:
        return True
    pe_ok = [to_float(row.get(k)) is not None for k in ("pe_dyn", "pe_static", "pe_ttm")]
    return 0 < sum(pe_ok) < 3


def _normalize_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    """兼容测试夹具里的原 clist 字段名（f12/f14/f2/f9/f114/f115/f133）。"""
    code = str(raw.get("f12") or raw.get("code") or raw.get("SECURITY_CODE") or "").strip().zfill(6)
    if not code.isdigit() or len(code) != 6:
        return None
    if "SECURITY_CODE" in raw or "CLOSE_PRICE" in raw:
        from dianjin.em_datacenter import normalize_datacenter_row

        return normalize_datacenter_row(raw)
    return {
        "code": code,
        "name": str(raw.get("f14") or raw.get("name") or "").strip(),
        "close": to_float(raw.get("f2") if "f2" in raw else raw.get("close")),
        "pe_dyn": to_float(raw.get("f9") if "f9" in raw else raw.get("pe_dyn")),
        "pe_static": to_float(raw.get("f114") if "f114" in raw else raw.get("pe_static")),
        "pe_ttm": to_float(raw.get("f115") if "f115" in raw else raw.get("pe_ttm")),
        "dividend": screen_yield_from_clist(raw)
        if "f133" in raw or "f183" in raw
        else to_float(raw.get("dividend")),
    }


def fetch_market_snapshot(
    *,
    page_size: int = 100,
    sleep_seconds: float = 0.15,
    timeout: float = 12.0,
) -> list[dict[str, Any]]:
    """拉取全 A 估值快照。失败抛 SnapshotError，不降级到缺三档 PE 的源。"""
    del page_size  # 数据中心按页 5000，保留参数以免改调用方
    rows = fetch_valuation_rows(timeout=max(timeout, 30.0), sleep_seconds=max(sleep_seconds, 0.3))
    if len(rows) < 1000:
        raise SnapshotError(f"估值快照收录过少（{len(rows)}），视为失败，不放宽市盈率规则")
    return rows


def fill_missing_valuation(rows: list[dict[str, Any]], *, timeout: float = 12.0) -> tuple[int, int]:
    """对缺 PE 的行用已缓存的数据中心快照补齐。返回 (补全数, 仍缺数)。只跑一次。"""
    del timeout
    missing = [row for row in rows if valuation_gappy(row)]
    if not missing:
        return 0, 0
    try:
        extras = {r["code"]: r for r in fetch_valuation_rows(use_cache=True)}
    except SnapshotError as exc:
        log.info("估值二次确认跳过：%s", exc)
        still = sum(1 for row in missing if valuation_incomplete(row))
        return 0, still
    filled = 0
    for row in missing:
        extra = extras.get(row.get("code"))
        if not extra:
            continue
        changed = False
        for key in ("close", "pe_dyn", "pe_static", "pe_ttm", "dividend", "name"):
            if key == "name":
                if not row.get("name") and extra.get("name"):
                    row["name"] = extra["name"]
                    changed = True
                continue
            if to_float(row.get(key)) is None and to_float(extra.get(key)) is not None:
                row[key] = extra[key]
                changed = True
        if changed and not valuation_incomplete(row):
            filled += 1
    still = sum(1 for row in missing if valuation_incomplete(row))
    log.info("估值二次确认：补全 %d，仍缺 %d（亏损/无股息会继续缺）", filled, still)
    return filled, still
