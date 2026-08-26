"""收盘后日报：自选股（独立文件夹）+ 点金术全市场。每次先读 settings.yaml。"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

from launcher.paths import (
    chdir_project_root,
    ensure_watchlist,
    last_report_path,
    tech_output_dir,
    watchlist_output_dir,
    watchlist_path,
)
from launcher.settings import dated_report_dir, load_settings
from launcher.watchlist_report import (
    COMBINED_MD,
    FUND_SUBDIR,
    STOCKS_SUBDIR,
    TECH_SUBDIR,
    WATCHLIST_DIR_NAME,
    prune_legacy_date_root_watchlist,
    run_watchlist_full_analysis,
)

log = logging.getLogger("launcher")


def _write_last_report(payload: dict) -> None:
    path = last_report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_last_report() -> dict:
    path = last_report_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def report_done_today() -> bool:
    data = load_last_report()
    return str(data.get("date") or "") == datetime.now().strftime("%Y%m%d") and bool(data.get("ok"))


def _link(report_root: Path, target: Path) -> str:
    try:
        return target.resolve().relative_to(report_root.resolve()).as_posix()
    except ValueError:
        return target.as_posix()


def run_silent_tech_refresh() -> int:
    """开机/托盘：只刷技术面到 data/tech_analysis，不写日报目录。失败只记日志。"""
    from tech_analysis.config import load_config as load_tech_cfg
    from tech_analysis.main import analyze_one
    from tech_analysis.market import remember_name, resolve_name
    from tech_analysis.network import RateLimiter, disable_proxies, enable_browser_tls
    from tech_analysis.report import StockResult, write_summary
    from tech_analysis.watchlist import load_watchlist

    chdir_project_root()
    settings = load_settings()
    if not settings.daily_update:
        log.info("配置 daily_update=false，跳过静默技术面更新")
        return 0
    wl = ensure_watchlist()
    items = load_watchlist(wl)
    if not items:
        log.error("自选股为空：%s", wl)
        return 1
    disable_proxies()
    enable_browser_tls()
    tech_cfg = load_tech_cfg()
    limiter = RateLimiter(1.5)
    out_root = tech_output_dir()
    out_root.mkdir(parents=True, exist_ok=True)
    results = []
    for item in items:
        if item.name:
            remember_name(item.code, item.name)
    for i, item in enumerate(items, 1):
        try:
            name = resolve_name(item.code, item.name, tech_cfg, limiter)
        except Exception:
            name = item.name or item.code
        log.info("[%d/%d] 静默技术面 %s %s", i, len(items), item.code, name)
        try:
            results.append(analyze_one(item.code, name, tech_cfg, limiter, out_root))
        except Exception as exc:
            log.error("静默技术面失败 %s：%s", item.code, exc)
            results.append(StockResult(code=item.code, name=name, ok=False, error=str(exc)))
    write_summary(results, out_root / "信号汇总.md", out_root / "信号汇总.csv")
    ok_n = sum(1 for r in results if r.ok)
    log.info("静默技术面完成：成功 %d / %d", ok_n, len(results))
    return 0 if ok_n else 1


def _watchlist_overview_links(report_root: Path, bundle) -> list[str]:
    watch_root = report_root / WATCHLIST_DIR_NAME
    if bundle.empty:
        return ["- 自选股：今日无符合（名单为空）"]
    links: list[str] = []
    for row in bundle.rows:
        stock = watch_root / STOCKS_SUBDIR / row.folder
        combined = stock / COMBINED_MD
        fund_md = stock / FUND_SUBDIR / "财务分析.md"
        tech_md = stock / TECH_SUBDIR / "分析报告.md"
        parts = [f"- {row.code} {row.name}："]
        bits = []
        if combined.is_file():
            bits.append(f"[综合分析]({_link(report_root, combined)})")
        if fund_md.is_file():
            bits.append(f"[基本面]({_link(report_root, fund_md)})")
        if tech_md.is_file():
            bits.append(f"[技术面]({_link(report_root, tech_md)})")
        parts.append(" · ".join(bits) if bits else "无输出")
        links.append("".join(parts))
    return links


def _copy_dianjin_tree(report_root: Path, dj_src: Path) -> tuple[Path, Path]:
    dj_dest = report_root / "点金术"
    dj_extra_dest = report_root / "点金术extra"
    for dest, src_name in ((dj_dest, "点金术"), (dj_extra_dest, "点金术extra")):
        src = dj_src / src_name
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        if src.exists():
            shutil.copytree(src, dest)
        else:
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "总表.md").write_text("今日无符合\n", encoding="utf-8")
            (dest / "个股").mkdir(parents=True, exist_ok=True)
    from dianjin.signal_summary import copy_summaries_to_daily_root

    copy_summaries_to_daily_root(report_root, dj_dest, dj_extra_dest)
    return dj_dest, dj_extra_dest


def write_daily_overview(
    report_root: Path,
    *,
    today: str,
    stamp: str,
    bundle,
    dj,
    dj_dest: Path,
    dj_extra_dest: Path,
) -> Path:
    watchlist_empty = bundle.empty
    items = bundle.items
    tech_ok = bundle.tech_ok
    tech_fail = bundle.tech_fail
    fund_ok_n = sum(1 for r in bundle.rows if r.finance_ok)
    fund_fails = bundle.fund_fails
    new_filings = bundle.new_filings
    overview_links = _watchlist_overview_links(report_root, bundle)
    dj_n = len(dj.screen.hits)
    dj_extra = len(dj.screen.extra)
    dj_error = dj.screen.error or ""
    wl_codes = {item.code.zfill(6) for item in items}
    dj_watch_hits = [h for h in dj.screen.hits if str(h.code).zfill(6) in wl_codes]
    dj_watch_extra = [h for h in dj.screen.extra if str(h.code).zfill(6) in wl_codes]
    watch_root = report_root / WATCHLIST_DIR_NAME

    overview = [
        f"# 日报 {today}",
        "",
        f"- 生成时间：{stamp}",
        f"- 输出目录：`{report_root}`",
        f"- 自选股：{len(items)} 只（独立文件夹 `{WATCHLIST_DIR_NAME}/`，与机械选股 `data/screening/`、点金术 `data/dianjin/` 均分开）",
        f"- 自选股技术面：成功 {len(tech_ok)} / 失败 {len(tech_fail)}"
        + ("；今日无符合" if watchlist_empty else ""),
        f"- 自选股基本面：有数据 {fund_ok_n} / 失败 {len(fund_fails)}"
        + ("；今日无符合" if watchlist_empty else ""),
        f"- 新财报：{('、'.join(new_filings) if new_filings else '无')}",
        f"- 点金术：{dj_n} 只；点金术extra：{dj_extra} 只"
        + ("；今日无符合" if dj_n == 0 else ""),
        f"- 点金术中的自选股：{len(dj_watch_hits)} 只（仅标注，筛选仍是全市场）",
        "",
        "## 汇总",
        "",
        "### 自选股",
        "",
        f"- [自选股总表]({_link(report_root, watch_root / '总表.md')})",
        f"- [基本面汇总]({_link(report_root, watch_root / '基本面汇总.md')})",
        f"- [技术面汇总]({_link(report_root, watch_root / '技术面汇总.md')})",
        "",
        "### 点金术",
        "",
        f"- [点金术总表]({_link(report_root, dj_dest / '总表.md')})",
        f"- [点金术名单]({_link(report_root, dj_dest / '点金术.md')})",
        f"- [点金术技术信号汇总]({_link(report_root, report_root / '点金术技术信号汇总.md')})",
        f"- [点金术extra 总表]({_link(report_root, dj_extra_dest / '总表.md')})",
        f"- [点金术extra 名单]({_link(report_root, dj_extra_dest / '点金术extra.md')})",
        f"- [点金术extra技术信号汇总]({_link(report_root, report_root / '点金术extra技术信号汇总.md')})",
        "",
        "## 自选股",
        "",
        "每只自选股含综合分析（基本面 + 技术面对照）以及两侧详报。"
        " 与点金术全市场筛选分开输出，不把 `userdata/watchlist.txt` 当作点金术股票池。",
        "",
        *overview_links,
        "",
        "## 点金术",
        "",
        "全市场东财估值快照 → 先过滤股息与三档市盈率 → 仅对幸存者用腾讯日线算 MA120 → 只对命中股做技术面/财务。"
        " extra 是加严子集，单独放在并列的 `点金术extra/` 文件夹。"
        " 与自选股、成长股筛选名单不混用；自选股命中只改文件夹名和总表标注。",
        "",
    ]
    if dj_error:
        overview += [f"- 筛选特判：{dj_error}", ""]
    if dj_n == 0:
        overview += ["今日无符合", ""]
    overview += [
        "## 点金术中的自选股",
        "",
    ]
    if watchlist_empty:
        overview += ["自选股名单为空，未标注。", ""]
    elif not dj_watch_hits:
        overview += ["今日点金术名单中无自选股。", ""]
    else:
        from dianjin.watchlist_mark import stock_folder_name

        for hit in dj_watch_hits:
            extra_note = (
                "（同时在 extra）"
                if str(hit.code).zfill(6) in {str(x.code).zfill(6) for x in dj_watch_extra}
                else ""
            )
            folder = stock_folder_name(hit.code, hit.name, True)
            overview.append(
                f"- **自选** {hit.code} {hit.name}{extra_note}："
                f"[个股报告]({_link(report_root, dj_dest / '个股' / folder / '个股报告.md')})"
            )
        overview += ["", "个股目录名为 `代码_名称_自选`，清理仍按 6 位代码匹配。", ""]
    dest = report_root / "总览.md"
    dest.write_text("\n".join(overview), encoding="utf-8")
    return dest


def run_daily_report() -> int:
    """生成详细日报到「日报集/<日期>/」。调用前须已按磁盘配置判断是否允许跑。"""
    from stock_screener.config import load_config as load_growth_cfg

    chdir_project_root()
    settings = load_settings()
    today = datetime.now().strftime("%Y%m%d")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    report_root = dated_report_dir(today, settings)
    report_root.mkdir(parents=True, exist_ok=True)
    prune_legacy_date_root_watchlist(report_root)

    bundle = run_watchlist_full_analysis(
        watch_roots=[
            report_root / WATCHLIST_DIR_NAME,
            watchlist_output_dir(),
        ],
        stamp=stamp,
        settings=settings,
    )
    if bundle.empty:
        log.warning(
            "自选股为空：%s。自选股目录记「今日无符合」，仍执行点金术全市场筛选。",
            bundle.wl_path,
        )

    log.info("点金术：全 A 股筛选（不使用 --hist-limit / --limit / --screen-only）")
    from dianjin.pipeline import run_dianjin

    growth_cfg = load_growth_cfg()
    dj = run_dianjin(growth_cfg)
    dj_src = Path(dj.out_dir)
    dj_dest, dj_extra_dest = _copy_dianjin_tree(report_root, dj_src)
    write_daily_overview(
        report_root,
        today=today,
        stamp=stamp,
        bundle=bundle,
        dj=dj,
        dj_dest=dj_dest,
        dj_extra_dest=dj_extra_dest,
    )

    archive = Path("data") / "daily_reports" / today
    if archive.exists():
        shutil.rmtree(archive, ignore_errors=True)
    shutil.copytree(report_root, archive)

    _write_last_report(
        {
            "date": today,
            "dir": str(report_root),
            "ok": True,
            "watchlist": str(bundle.wl_path),
            "watchlist_empty": bundle.empty,
            "watchlist_dir": str(report_root / WATCHLIST_DIR_NAME),
            "tech_ok": len(bundle.tech_ok),
            "fund_ok": sum(1 for r in bundle.rows if r.finance_ok),
            "dianjin": len(dj.screen.hits),
            "dianjin_extra": len(dj.screen.extra),
            "dianjin_dir": str(dj_src),
            "dianjin_error": dj.screen.error or "",
        }
    )
    log.info(
        "日报已写入：%s（自选股 %d，点金术 %d / extra %d）",
        report_root,
        len(bundle.items),
        len(dj.screen.hits),
        len(dj.screen.extra),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    force = "--force" in args
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    settings = load_settings()
    if not settings.daily_update and not force:
        log.info("配置 daily_update=false（%s），跳过日报，不写桌面", watchlist_path().parent / "settings.yaml")
        return 0
    return run_daily_report()
