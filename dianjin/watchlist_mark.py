"""点金术命中股若也在自选股中，仅做标注，不改筛选股票池。"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("dianjin")

WATCHLIST_MARK = "自选"
WATCHLIST_SUFFIX = f"_{WATCHLIST_MARK}"


def load_watchlist_codes(path: str | Path | None = None) -> set[str]:
    """读取自选股代码。文件缺失、空名单或解析失败时返回空集合，不抛错。"""
    try:
        from tech_analysis.watchlist import load_watchlist

        if path is None:
            try:
                from launcher.paths import watchlist_path

                dest = watchlist_path()
            except Exception:
                dest = Path("userdata") / "watchlist.txt"
        else:
            dest = Path(path)
        if not dest.is_file():
            return set()
        return {item.code.zfill(6) for item in load_watchlist(dest)}
    except FileNotFoundError:
        return set()
    except Exception as exc:
        log.info("自选股名单不可用，点金术不标注：%s", exc)
        return set()


def in_watchlist(code: str, watchlist_codes: set[str] | None) -> bool:
    if not watchlist_codes:
        return False
    return str(code).zfill(6) in {str(c).zfill(6) for c in watchlist_codes}


def stock_folder_name(code: str, name: str, marked: bool) -> str:
    from tech_analysis.report import stock_dir_name

    base = stock_dir_name(str(code).zfill(6), name or "")
    if marked and not base.endswith(WATCHLIST_SUFFIX):
        return f"{base}{WATCHLIST_SUFFIX}"
    return base


def preferred_folder_map(hits, watchlist_codes: set[str] | None) -> dict[str, str]:
    codes = watchlist_codes or set()
    out: dict[str, str] = {}
    for hit in hits or []:
        code = str(getattr(hit, "code", "")).zfill(6)
        if not code or not code.isdigit():
            continue
        name = str(getattr(hit, "name", "") or "")
        out[code] = stock_folder_name(code, name, in_watchlist(code, codes))
    return out
