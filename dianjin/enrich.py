"""仅对最终命中股做技术面 + 财务（不给只过股息/PE 的名字拉 K 线增强）。"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from dianjin.screen import DianjinHit
from dianjin.tech_summary import (
    cells_from_daily_csv,
    format_tech_cells,
    last_two_indicator_rows,
    merge_snapshot,
    overview_lines,
    _row_from_series,
)

log = logging.getLogger("dianjin")

TECH_FILES = ("分析报告.md", "技术分析.png", "signals.csv", "日线指标.csv")


def _docs_stock_dir(cfg: dict, code: str, name: str) -> Path:
    from stock_screener.config import cfg_get
    from stock_screener.downloader import sanitize_filename

    base = Path(cfg_get(cfg, "downloads.output_dir", "data/docs"))
    if base.exists():
        matches = sorted(
            p for p in base.iterdir() if p.is_dir() and p.name.startswith(f"{code}_")
        )
        if matches:
            return matches[0]
        exact = base / code
        if exact.is_dir():
            return exact
    label = f"{code}_{sanitize_filename(name, 20)}" if name else code
    return base / label


def never_downloaded_finance(stock_dir: Path) -> bool:
    """从未落过财务：既无 财务数据.csv，也无任何财报 PDF。"""
    csv_path = Path(stock_dir) / "财务分析" / "财务数据.csv"
    pdf_dir = Path(stock_dir) / "财报"
    has_csv = csv_path.is_file()
    has_pdf = False
    if pdf_dir.is_dir():
        try:
            has_pdf = any(pdf_dir.glob("*.pdf"))
        except OSError:
            has_pdf = False
    return (not has_csv) and (not has_pdf)


def _copy_named(src: Path, dest: Path, names: tuple[str, ...] | list[str]) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in names:
        item = src / name
        if item.is_file():
            shutil.copy2(item, dest / name)
            copied.append(name)
    return copied


def _recent_trading_dates(daily_csv: Path, n: int) -> set[str]:
    if not daily_csv.is_file() or n <= 0:
        return set()
    try:
        import pandas as pd

        df = pd.read_csv(daily_csv, encoding="utf-8-sig")
    except Exception:
        return set()
    if df.empty or "date" not in df.columns:
        return set()
    dates = [str(v)[:10] for v in df["date"].tolist() if str(v).strip()]
    return set(dates[-n:])


def _format_recent_signals(signals, recent_dates: set[str]) -> str:
    from tech_analysis.report import _date_str

    picked = [s for s in signals if _date_str(s.date) in recent_dates]
    if not picked:
        return "无"
    parts = [f"{_date_str(s.date)} {s.signal_type}" for s in picked]
    return "；".join(parts)


def _write_stock_overview(
    dest: Path,
    hit: DianjinHit,
    *,
    recent_text: str,
    tech_ok: bool,
    tech_error: str,
    fund_ok: bool,
    fund_note: str,
    recent_days: int,
    in_watchlist: bool = False,
    tech_cells: dict | None = None,
) -> None:
    extra_flag = "是（点金术extra）" if hit.is_extra else "否"
    watch_flag = "是（目录名带 `_自选`）" if in_watchlist else "否"
    lines = [
        f"# {hit.code} {hit.name} 点金术个股报告",
        "",
        f"- 代码 / 名称：{hit.code} {hit.name}",
        f"- 是否 extra：{extra_flag}",
        f"- 是否自选股：{watch_flag}（仅标注，筛选不读自选股名单）",
        f"- 收盘（前复权 K 线）：{hit.close:.4f}",
        f"- MA120：{hit.ma120:.4f}",
        f"- 收盘/MA120：{hit.close_ma_ratio:.4f}（点金术要求严格小于 0.88；extra 严格小于 0.82）",
        f"- 股息率（同花顺股息率TTM，筛选口径）：{hit.dividend:.2f}%",
        f"- 市盈率 动态 / 静态 / TTM：{hit.pe_dyn:.2f} / {hit.pe_static:.2f} / {hit.pe_ttm:.2f}",
        f"- 近 {recent_days} 个交易日技术信号：**{recent_text}**",
        "",
        *overview_lines(tech_cells or {}),
        "## 材料",
        "",
        "- [技术面分析报告](分析报告.md)" if tech_ok else f"- 技术面失败：{tech_error or '—'}",
        "- [财务分析](财务分析.md)" if fund_ok else f"- 财务：{fund_note or '未生成'}",
        "",
        "## 说明",
        "",
        "- 技术面图为最近 **120 个交易日** K 线，叠加 MA8 / MA24 / MA120；"
        "BOLL(20,2)、MACD(12,26,9)、RSI(6/12/24，通达信 RSI1/2/3)。",
        "- 财务优先用本地 `data/docs` 已有的 `财务数据.csv` / 图表；若从未下载（无 CSV 且无财报 PDF），"
        "当日才拉东财公开财报接口，并只补**最新一期**定期报告 PDF。",
        "- **不会**为点金术名单批量下载历史招股书、全部定期报告或研报（那会把日报拖成数小时）。",
        "- extra 是点金术的加严子集；点金术与点金术extra 各有独立文件夹和个股副本。",
        "- 筛选只用同花顺股息率TTM（快照字段为东财 f133，与之同列）。"
        "不用 f183，不用同花顺 526792（振幅），也不用腾讯年度股息率。",
        "",
    ]
    dest.write_text("\n".join(lines), encoding="utf-8")


def _tech_complete(dest: Path) -> bool:
    return all((dest / name).is_file() for name in TECH_FILES)


def _finance_complete(dest: Path) -> bool:
    from stock_screener.finance import FINANCE_BUNDLE_FILES

    return (dest / "财务分析.md").is_file() and (dest / "财务数据.csv").is_file() and any(
        (dest / name).is_file() for name in FINANCE_BUNDLE_FILES if name.endswith(".png")
    )


def _enrich_one(
    hit: DianjinHit,
    dest: Path,
    cfg: dict,
    tech_cfg: dict,
    limiter,
    stocks_root: Path,
    recent_days: int,
    *,
    do_tech: bool,
    do_fund: bool,
    in_watchlist: bool = False,
) -> dict:
    from stock_screener.finance import FINANCE_BUNDLE_FILES, analyze_stock_finance, finance_out_dir
    from tech_analysis.main import analyze_one
    from tech_analysis.report import StockResult

    tech_ok = _tech_complete(dest)
    tech_error = ""
    recent_text = "无"
    result: StockResult | None = None
    if do_tech:
        try:
            result = analyze_one(hit.code, hit.name, tech_cfg, limiter, stocks_root)
            tech_ok = bool(result.ok and result.out_dir)
            if result.out_dir and result.out_dir.resolve() != dest.resolve():
                _copy_named(result.out_dir, dest, TECH_FILES)
            tech_ok = _tech_complete(dest) or tech_ok
        except Exception as exc:
            tech_error = str(exc)
            log.error("  技术面失败 %s：%s", hit.code, exc)
    daily_csv = dest / "日线指标.csv"
    recent_dates = _recent_trading_dates(daily_csv, recent_days)
    if result and result.signals:
        recent_text = _format_recent_signals(result.signals, recent_dates)
    elif (dest / "signals.csv").is_file():
        recent_text = "见 signals.csv"

    csv_last, csv_prev = last_two_indicator_rows(daily_csv)
    result_snap = _row_from_series(getattr(result, "snapshot", None) or {})
    if not result_snap.get("date") and getattr(result, "last_date", ""):
        result_snap["date"] = result.last_date
    if result_snap.get("close") is None and getattr(result, "last_close", None) is not None:
        result_snap["close"] = result.last_close
    tech_snap = merge_snapshot(result_snap, csv_last)
    tech_cells = format_tech_cells(tech_snap, csv_prev)
    if tech_cells["指标日"] == "—" and daily_csv.is_file():
        tech_cells = cells_from_daily_csv(daily_csv)

    docs_dir = _docs_stock_dir(cfg, hit.code, hit.name)
    fund_ok = _finance_complete(dest)
    fund_note = ""
    if do_fund:
        if never_downloaded_finance(docs_dir):
            log.info("  财务从未下载，拉取公开财报接口 + 最新一期定期 PDF（不下全量历史）")
        try:
            fund_ok = bool(analyze_stock_finance(hit.code, hit.name, docs_dir, cfg))
        except Exception as exc:
            fund_note = str(exc)
            log.error("  财务失败 %s：%s", hit.code, exc)
        copied = _copy_named(finance_out_dir(docs_dir), dest, FINANCE_BUNDLE_FILES)
        if copied:
            fund_ok = True
            log.info("  已写入财务文件：%s", "、".join(copied))
        elif not fund_note:
            fund_note = "无财务输出"
        fund_ok = _finance_complete(dest) or fund_ok

    _write_stock_overview(
        dest / "个股报告.md",
        hit,
        recent_text=recent_text,
        tech_ok=tech_ok,
        tech_error=tech_error,
        fund_ok=fund_ok,
        fund_note=fund_note,
        recent_days=recent_days,
        in_watchlist=in_watchlist,
        tech_cells=tech_cells,
    )
    last_date = ""
    if result and getattr(result, "last_date", ""):
        last_date = str(result.last_date)[:10]
    elif tech_cells.get("指标日") and tech_cells["指标日"] != "—":
        last_date = str(tech_cells["指标日"])[:10]
    signals = list(result.signals) if result and getattr(result, "signals", None) else []
    return {
        "hit": hit,
        "folder": dest.name,
        "recent_signals": recent_text,
        "tech_ok": tech_ok,
        "fund_ok": fund_ok,
        "in_watchlist": in_watchlist,
        "tech_cells": tech_cells,
        "tech_snap": tech_snap,
        "tech_prev": csv_prev,
        "signals": signals,
        "last_date": last_date,
    }


def _ensure_stock_dir(stocks_root: Path, code: str, name: str, marked: bool) -> Path:
    from dianjin.report import _folder_code
    from dianjin.watchlist_mark import stock_folder_name

    wanted = stock_folder_name(code, name, marked)
    dest = stocks_root / wanted
    if stocks_root.is_dir():
        for item in list(stocks_root.iterdir()):
            if not item.is_dir():
                continue
            if _folder_code(item.name) != str(code).zfill(6):
                continue
            if item.name == wanted:
                continue
            if not dest.exists():
                try:
                    item.rename(dest)
                except OSError:
                    shutil.copytree(item, dest)
                    shutil.rmtree(item, ignore_errors=True)
            else:
                shutil.rmtree(item, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def enrich_hits(
    hits: list[DianjinHit],
    out_dir: Path,
    cfg: dict,
    *,
    recent_signal_days: int = 5,
    watchlist_codes: set[str] | None = None,
) -> list[dict]:
    """写出 个股/<code_name[_自选]>/ 技术面+财务+个股报告。返回供总表使用的行信息。"""
    from dianjin.watchlist_mark import in_watchlist as code_in_watchlist
    from tech_analysis.config import load_config as load_tech_cfg
    from tech_analysis.network import RateLimiter, disable_proxies, enable_browser_tls

    disable_proxies()
    enable_browser_tls()
    tech_cfg = load_tech_cfg()
    limiter = RateLimiter(1.5)
    stocks_root = Path(out_dir) / "个股"
    stocks_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    recent_days = max(1, int(recent_signal_days))
    wl = watchlist_codes or set()

    for i, hit in enumerate(hits, 1):
        marked = code_in_watchlist(hit.code, wl)
        dest = _ensure_stock_dir(stocks_root, hit.code, hit.name, marked)
        log.info("[%d/%d] 增强 %s %s%s", i, len(hits), hit.code, hit.name, "（自选）" if marked else "")
        rows.append(
            _enrich_one(
                hit,
                dest,
                cfg,
                tech_cfg,
                limiter,
                stocks_root,
                recent_days,
                do_tech=True,
                do_fund=True,
                in_watchlist=marked,
            )
        )

    need = [
        (hit, stocks_root / row["folder"], row)
        for hit, row in zip(hits, rows)
        if (not row.get("tech_ok")) or (not row.get("fund_ok"))
    ]
    if need:
        log.info("增强二次确认：%d 只缺技术面/财务，再跑一轮（日线腾讯优先）", len(need))
        filled = 0
        for hit, dest, row in need:
            dest.mkdir(parents=True, exist_ok=True)
            again = _enrich_one(
                hit,
                dest,
                cfg,
                tech_cfg,
                limiter,
                stocks_root,
                recent_days,
                do_tech=not row.get("tech_ok"),
                do_fund=not row.get("fund_ok"),
                in_watchlist=bool(row.get("in_watchlist")),
            )
            row.update(again)
            if again.get("tech_ok") and again.get("fund_ok"):
                filled += 1
        still = sum(1 for _hit, _dest, row in need if not (row.get("tech_ok") and row.get("fund_ok")))
        log.info("增强二次确认：补全 %d，仍缺 %d", filled, still)
    return rows
