"""点金术日报 / CLI 入口。生产路径不做 hist-limit / limit / screen-only。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from dianjin.enrich import enrich_hits
from dianjin.report import (
    copy_stock_dirs,
    extra_dir,
    main_dir,
    prune_stale_stock_dirs,
    write_reports,
)
from dianjin.rules import DEFAULTS
from dianjin.screen import ScreenResult, screen_market

log = logging.getLogger("dianjin")


@dataclass
class DianjinRunResult:
    returncode: int
    out_dir: Path
    screen: ScreenResult = field(default_factory=ScreenResult)
    enriched: int = 0


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d")


def _default_out_dir(cfg: dict[str, Any] | None, stamp: str) -> Path:
    from stock_screener.config import cfg_get

    try:
        from launcher.paths import dianjin_dir

        return dianjin_dir(stamp)
    except Exception:
        root = Path(cfg_get(cfg or {}, "paths.dianjin_dir", "data/dianjin"))
        return root / stamp


def run_dianjin(
    cfg: dict[str, Any] | None = None,
    *,
    screen_only: bool = False,
    limit: int | None = None,
    hist_limit: int | None = None,
    codes: list[str] | None = None,
    out_dir: str | Path | None = None,
    snapshot_rows: list[dict[str, Any]] | None = None,
) -> DianjinRunResult:
    """筛选并写报告。

    `--hist-limit` / `--limit` / `--screen-only` / `--codes` 仅供测试或手工试跑。
    日报必须全市场快照，并对全部股息+PE 幸存者拉 MA120、对全部命中股增强。
    """
    os.environ.setdefault("MPLBACKEND", "Agg")
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    if cfg is None:
        from stock_screener.config import load_config

        cfg = load_config()

    day = _stamp()
    dest = Path(out_dir) if out_dir is not None else _default_out_dir(cfg, day)
    dest.mkdir(parents=True, exist_ok=True)

    if hist_limit is not None or limit is not None or screen_only or codes:
        log.warning(
            "点金术测试/手工开关：screen_only=%s hist_limit=%s limit=%s codes=%s（日报不得使用）",
            screen_only,
            hist_limit,
            limit,
            ",".join(codes) if codes else "",
        )

    screen = screen_market(cfg, codes=codes, hist_limit=hist_limit, snapshot_rows=snapshot_rows)
    hits = list(screen.hits)
    if limit is not None and int(limit) >= 0:
        hits = hits[: int(limit)]
        screen.hits = hits
        screen.extra = [h for h in hits if h.is_extra]
        log.warning("测试用 --limit=%d，只保留前 %d 只命中（生产日报不得使用）", int(limit), len(hits))

    extra_hits = [h for h in hits if h.is_extra]
    main_path = main_dir(dest)
    extra_path = extra_dir(dest)
    main_path.mkdir(parents=True, exist_ok=True)
    extra_path.mkdir(parents=True, exist_ok=True)
    from dianjin.watchlist_mark import load_watchlist_codes, preferred_folder_map

    watchlist_codes = load_watchlist_codes()
    prune_stale_stock_dirs(main_path, [h.code for h in hits], label="点金术")
    prune_stale_stock_dirs(extra_path, [h.code for h in extra_hits], label="点金术extra")

    enrich_info: list[dict] = []
    extra_info: list[dict] = []
    if hits and not screen_only:
        recent_days = int((cfg.get("dianjin") or {}).get("recent_signal_days", DEFAULTS["recent_signal_days"]))
        enrich_info = enrich_hits(
            hits, main_path, cfg, recent_signal_days=recent_days, watchlist_codes=watchlist_codes
        )
        copied = copy_stock_dirs(main_path, extra_path, [h.code for h in extra_hits])
        extra_info = [row for row in enrich_info if getattr(row.get("hit"), "is_extra", False)]
        log.info("点金术extra 已写入独立文件夹：%s（个股副本 %d）", extra_path, copied)
    elif not hits:
        log.info("点金术今日无符合，写特判报告后继续")

    preferred_main = preferred_folder_map(hits, watchlist_codes)
    preferred_extra = preferred_folder_map(extra_hits, watchlist_codes)
    prune_stale_stock_dirs(
        main_path, [h.code for h in hits], label="点金术", preferred_names=preferred_main
    )
    prune_stale_stock_dirs(
        extra_path, [h.code for h in extra_hits], label="点金术extra", preferred_names=preferred_extra
    )

    write_reports(
        screen,
        dest,
        stamp=day,
        enrich_info=enrich_info,
        extra_enrich_info=extra_info,
        screen_only=screen_only or not hits,
        watchlist_codes=watchlist_codes,
    )
    log.info("点金术报告已写入：%s（命中 %d，extra %d）", dest, len(screen.hits), len(screen.extra))
    return DianjinRunResult(
        returncode=0,
        out_dir=dest,
        screen=screen,
        enriched=len(enrich_info),
    )
