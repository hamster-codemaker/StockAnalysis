"""自选股全量分析：技术面 + 基本面 + 综合分析，写入独立「自选股」目录。

日报、GUI「分析全部自选股」、CLI `--watchlist-analyze` / `watchlist` 共用本模块。
不跑点金术，也不把自选股名单当作点金术股票池。
"""

from __future__ import annotations

import logging
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from launcher.paths import (
    chdir_project_root,
    docs_dir,
    ensure_watchlist,
    tech_output_dir,
    watchlist_output_dir,
)
from launcher.settings import dated_report_dir, load_settings

log = logging.getLogger("launcher")

WATCHLIST_DIR_NAME = "自选股"
EMPTY_MARK = "今日无符合"
TECH_SUBDIR = "技术面"
FUND_SUBDIR = "基本面"
COMBINED_MD = "综合分析.md"
STOCKS_SUBDIR = "个股"
TECH_BUNDLE = ("分析报告.md", "技术分析.png", "signals.csv", "日线指标.csv")
LEGACY_DATE_ROOT = ("技术面", "基本面", "技术面汇总.md", "基本面汇总.md")
WATCHLIST_ANALYZE_FLAGS = ("--watchlist-analyze", "watchlist")


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    sep = "|" + " --- |" * len(headers)
    if not rows:
        rows = [["—"] * len(headers)]
    body = ["| " + " | ".join("" if v is None else str(v) for v in row) + " |" for row in rows]
    return "\n".join([head, sep, *body])


def _fmt_pct(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if pd.isna(number):
        return "—"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.1f}%"


def _fmt_yi_from_wan(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    try:
        return f"{float(value) / 1e4:.2f} 亿元"
    except (TypeError, ValueError):
        return "—"


def _fmt_num(value, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _copy_named(src: Path, dest: Path, names: tuple[str, ...] | list[str]) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in names:
        item = src / name
        if item.is_file():
            shutil.copy2(item, dest / name)
            copied.append(name)
    return copied


def _stock_docs_dir(code: str, name: str) -> Path:
    from stock_screener.downloader import sanitize_filename

    root = docs_dir()
    if root.exists():
        matches = sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith(f"{code}_"))
        if matches:
            return matches[0]
        exact = root / code
        if exact.is_dir():
            return exact
    label = f"{code}_{sanitize_filename(name, 20)}" if name else code
    return root / label


def latest_finance_row(csv_path: Path) -> dict:
    if not csv_path.is_file():
        return {}
    try:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
    except Exception:
        return {}
    if df.empty or "报告期" not in df.columns:
        return {}
    df = df.sort_values("报告期", ascending=False)
    return df.iloc[0].to_dict()


def _looks_like_dated_report(path: Path) -> bool:
    name = Path(path).name
    return len(name) == 8 and name.isdigit()


def prune_legacy_date_root_watchlist(report_root: Path) -> None:
    """日报日期根下不再放 技术面/基本面，避免与点金术、自选股并列时混淆。"""
    root = Path(report_root)
    if not _looks_like_dated_report(root):
        return
    for name in LEGACY_DATE_ROOT:
        path = root / name
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.is_file():
                path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("清理旧日报自选股目录失败 %s：%s", path, exc)


def _reset_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class WatchlistStockRow:
    code: str
    name: str
    folder: str
    tech: object
    finance: dict = field(default_factory=dict)
    finance_ok: bool = False
    finance_error: str = ""
    fetched_new: bool = False
    old_period: str = ""
    tech_src: Path | None = None
    fund_src: Path | None = None


@dataclass
class WatchlistBundle:
    empty: bool
    items: list
    rows: list[WatchlistStockRow]
    wl_path: Path
    stamp: str
    earnings: bool
    fund_fails: list[list[str]] = field(default_factory=list)
    new_filings: list[str] = field(default_factory=list)
    roots: list[Path] = field(default_factory=list)

    @property
    def tech_ok(self) -> list[WatchlistStockRow]:
        return [r for r in self.rows if getattr(r.tech, "ok", False)]

    @property
    def tech_fail(self) -> list[WatchlistStockRow]:
        return [r for r in self.rows if not getattr(r.tech, "ok", False)]


def write_combined_analysis(
    dest: Path,
    *,
    code: str,
    name: str,
    stamp: str,
    tech,
    finance: dict | None,
    finance_ok: bool,
    finance_error: str = "",
    fund_md_exists: bool = False,
    tech_md_exists: bool = False,
) -> Path:
    """根据已有技术面结果与财务快照写 综合分析.md。缺数如实写，不编估值。"""
    from tech_analysis.report import (
        BOLL_TYPES,
        MA_TYPES,
        MACD_CROSS_TYPES,
        MACD_DIV_TYPES,
        RSI_TYPES,
        _latest_text,
    )

    finance = finance or {}
    title = f"{code} {name}".strip()
    tech_ok = bool(getattr(tech, "ok", False))
    last_date = str(getattr(tech, "last_date", "") or "")
    last_close = getattr(tech, "last_close", None)
    signals = list(getattr(tech, "signals", None) or [])
    snap = dict(getattr(tech, "snapshot", None) or {})
    tech_error = str(getattr(tech, "error", "") or "")

    period = str(finance.get("报告期") or "")
    ptype = str(finance.get("报告类型") or "")
    rev = _fmt_yi_from_wan(finance.get("营业收入(万元)"))
    np_ = _fmt_yi_from_wan(finance.get("归母净利润(万元)"))
    rev_yoy = _fmt_pct(finance.get("营业收入同比(%)"))
    np_yoy = _fmt_pct(finance.get("归母净利润同比(%)"))

    highlights: list[str] = []
    if finance_ok and period:
        highlights.append(
            f"最新财报 **{period} {ptype}**：营收 {rev}（同比 {rev_yoy}），"
            f"归母净利 {np_}（同比 {np_yoy}）。"
        )
    elif finance_error:
        highlights.append(f"基本面不可用：{finance_error}")
    else:
        highlights.append("基本面数据缺失，未写入估值或盈利预测。")

    if tech_ok:
        close_txt = _fmt_num(last_close, 2) if last_close is not None else "—"
        today_hit = any(str(getattr(s, "date", ""))[:10] == last_date for s in signals)
        hit_txt = "当日有技术信号" if today_hit else "当日未触发上述技术信号"
        highlights.append(
            f"技术面最新交易日 **{last_date or '—'}** 收盘 {close_txt}；{hit_txt}。"
        )
    elif tech_error:
        highlights.append(f"技术面失败：{tech_error}")
    else:
        highlights.append("技术面数据缺失，未套用点金术股息/市盈率/MA120 规则。")

    if not (finance_ok and tech_ok):
        highlights.append("两侧材料不齐，本页只做已有信息对照，不补估缺失字段。")

    fund_rows = [
        ["报告期", f"{period} {ptype}".strip() or "—"],
        ["营业收入", rev],
        ["营收同比 / 环比", f"{rev_yoy} / {_fmt_pct(finance.get('营业收入环比(%)'))}"],
        ["归母净利润", np_],
        ["净利同比 / 环比", f"{np_yoy} / {_fmt_pct(finance.get('归母净利润环比(%)'))}"],
        ["扣非净利润", _fmt_yi_from_wan(finance.get("扣非净利润(万元)"))],
        ["毛利率 / 净利率 / ROE", (
            f"{_fmt_pct(finance.get('毛利率(%)'))} / "
            f"{_fmt_pct(finance.get('净利率(%)'))} / "
            f"{_fmt_pct(finance.get('ROE(%)'))}"
        )],
        ["经营现金流净额", _fmt_yi_from_wan(finance.get("经营现金流净额(万元)"))],
        ["资产负债率 / 流动比率", (
            f"{_fmt_pct(finance.get('资产负债率(%)'))} / "
            f"{_fmt_num(finance.get('流动比率'), 2)}"
        )],
        ["基本每股收益", (
            f"{_fmt_num(finance.get('基本每股收益(元)'), 2)} 元"
            if finance.get("基本每股收益(元)") not in (None, "")
            else "—"
        )],
    ]
    if not finance:
        fund_block = "无财务快照（本地 CSV 不存在或无法解析）。"
    else:
        fund_block = _md_table(["项目", "数值"], fund_rows)

    if tech_ok:
        tech_rows = [
            ["最新交易日", snap.get("date") or last_date or "—"],
            ["收盘价", _fmt_num(snap.get("close", last_close), 2)],
            ["MA8 / MA24 / MA120", (
                f"{_fmt_num(snap.get('ma_short'))} / "
                f"{_fmt_num(snap.get('ma_long'))} / "
                f"{_fmt_num(snap.get('ma_trend'))}"
            )],
            ["DIF / DEA / HIST", (
                f"{_fmt_num(snap.get('dif'), 4)} / "
                f"{_fmt_num(snap.get('dea'), 4)} / "
                f"{_fmt_num(snap.get('hist'), 4)}"
            )],
            ["布林上 / 中 / 下", (
                f"{_fmt_num(snap.get('boll_upper'))} / "
                f"{_fmt_num(snap.get('boll_mid'))} / "
                f"{_fmt_num(snap.get('boll_lower'))}"
            )],
            ["RSI6 / RSI12 / RSI24", (
                f"{_fmt_num(snap.get('rsi'), 2)} / "
                f"{_fmt_num(snap.get('rsi2'), 2)} / "
                f"{_fmt_num(snap.get('rsi3'), 2)}"
            )],
            ["最近 MA 金死叉", _latest_text(signals, MA_TYPES)],
            ["最近 MACD 金死叉", _latest_text(signals, MACD_CROSS_TYPES)],
            ["最近 MACD 背离", _latest_text(signals, MACD_DIV_TYPES)],
            ["最近布林破轨", _latest_text(signals, BOLL_TYPES)],
            ["最近 RSI", _latest_text(signals, RSI_TYPES)],
        ]
        tech_block = _md_table(["项目", "数值"], tech_rows)
    else:
        tech_block = f"技术面未生成。{tech_error or '无可用信号与指标快照。'}"

    fund_link = (
        "- [财务分析（详细）](基本面/财务分析.md)"
        if fund_md_exists
        else "- 财务分析.md 未生成"
    )
    tech_link = (
        "- [技术面分析报告（详细）](技术面/分析报告.md)"
        if tech_md_exists
        else "- 分析报告.md 未生成"
    )

    lines = [
        f"# {title} 综合分析",
        "",
        f"- 生成时间：{stamp}",
        f"- 代码 / 名称：{code} {name}".rstrip(),
        "- 范围：自选股基本面 + 技术面对照；**不是**点金术筛选结果，也不构成买卖建议。",
        "- 缺数如实记录，不估算合理估值，不套用点金术股息 / 三档市盈率 / MA120 折价规则。",
        "",
        "## 综合要点",
        "",
        *[f"- {line}" for line in highlights],
        "",
        "## 基本面快照",
        "",
        fund_block,
        "",
        "## 技术面快照",
        "",
        tech_block,
        "",
        "## 详细报告",
        "",
        fund_link,
        tech_link,
        "",
        "## 说明",
        "",
        "- 财务来自东方财富公开财报接口或本地 `财务数据.csv`；**未解析**扫描版 PDF。",
        "- 技术面为最近 120 个交易日 K 线，叠加 MA8/MA24/MA120、BOLL(20,2)、MACD(12,26,9)、RSI(6/12/24)。",
        "- 更细的趋势图、历史多期表与信号明细见上方链接，本页只做两侧摘要对照。",
        "",
    ]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


def _tech_summary_lines(bundle: WatchlistBundle) -> list[str]:
    from tech_analysis.report import (
        BOLL_TYPES,
        MA_TYPES,
        MACD_CROSS_TYPES,
        MACD_DIV_TYPES,
        RSI_TYPES,
        _latest_text,
        stock_dir_name,
    )

    lines = [
        "# 自选股技术面汇总",
        "",
        f"- 生成时间：{bundle.stamp}",
        f"- 自选股文件：`{bundle.wl_path}`",
        f"- 共 {len(bundle.items)} 只，成功 {len(bundle.tech_ok)}，失败 {len(bundle.tech_fail)}",
        "- 本目录是自选股分析，与点金术全市场筛选分开存放。",
        "",
    ]
    if bundle.empty:
        lines += [EMPTY_MARK, "", "自选股名单为空，已跳过技术面；点金术若随日报执行仍按全市场扫描。", ""]
        return lines

    table = []
    for row in bundle.tech_ok:
        r = row.tech
        folder = row.folder or stock_dir_name(r.code, r.name)
        detail = f"[详细]({STOCKS_SUBDIR}/{folder}/{TECH_SUBDIR}/分析报告.md)"
        combined = f"[综合]({STOCKS_SUBDIR}/{folder}/{COMBINED_MD})"
        today_hit = "是" if any(str(s.date)[:10] == r.last_date for s in r.signals) else "否"
        table.append(
            [
                r.code,
                r.name,
                r.last_date,
                f"{r.last_close:.2f}" if r.last_close is not None else "—",
                _latest_text(r.signals, MA_TYPES),
                _latest_text(r.signals, MACD_CROSS_TYPES),
                _latest_text(r.signals, MACD_DIV_TYPES),
                _latest_text(r.signals, BOLL_TYPES),
                _latest_text(r.signals, RSI_TYPES),
                today_hit,
                f"{combined} · {detail}",
            ]
        )
    lines += [
        "## 总览",
        "",
        _md_table(
            ["代码", "名称", "交易日", "收盘", "MA金死叉", "MACD金死叉", "MACD背离", "布林破轨", "RSI", "当日触发", "详细"],
            table,
        ),
        "",
        "上表为回看期内各类**最近一次**信号。各股目录含综合分析、完整信号表、指标快照与技术分析图。",
        "",
    ]
    if bundle.tech_fail:
        lines += [
            "## 失败",
            "",
            _md_table(
                ["代码", "名称", "原因"],
                [[r.tech.code, r.tech.name, r.tech.error] for r in bundle.tech_fail],
            ),
            "",
        ]
    return lines


def _fund_summary_lines(bundle: WatchlistBundle) -> list[str]:
    lines = [
        "# 自选股基本面汇总",
        "",
        f"- 生成时间：{bundle.stamp}",
        f"- 财报季：{'是' if bundle.earnings else '否'}",
        "- 数据：东方财富公开财报接口；本地已是最新则不出网硬刷；**未解析**扫描版 PDF",
        "- 本目录是自选股分析，与点金术全市场筛选分开存放。",
        "",
    ]
    if bundle.empty:
        lines += [EMPTY_MARK, "", "自选股名单为空，已跳过基本面；点金术若随日报执行仍按全市场扫描。", ""]
        return lines

    fund_rows = []
    for row in bundle.rows:
        latest = row.finance
        if not latest:
            continue
        period = str(latest.get("报告期") or "")
        ptype = str(latest.get("报告类型") or "")
        badge = " **新财报**" if row.fetched_new else ""
        fund_rows.append(
            [
                row.code,
                row.name,
                f"{period} {ptype}{badge}".strip(),
                _fmt_yi_from_wan(latest.get("营业收入(万元)")),
                _fmt_pct(latest.get("营业收入同比(%)")),
                _fmt_pct(latest.get("营业收入环比(%)")),
                _fmt_yi_from_wan(latest.get("归母净利润(万元)")),
                _fmt_pct(latest.get("归母净利润同比(%)")),
                _fmt_pct(latest.get("归母净利润环比(%)")),
                f"[详细]({STOCKS_SUBDIR}/{row.folder}/{FUND_SUBDIR}/财务分析.md)",
            ]
        )
    lines += [
        "## 最新一期对照",
        "",
        _md_table(
            ["代码", "名称", "报告期", "营收", "营收同比", "营收环比", "归母净利", "净利同比", "净利环比", "详细"],
            fund_rows,
        ),
        "",
        "各股目录含综合分析、最近一期要点、趋势图、历史多期表与财务数据.csv。",
        "",
    ]
    if bundle.new_filings:
        lines += ["## 新财报", "", *[f"- {x}" for x in bundle.new_filings], ""]
    if bundle.fund_fails:
        lines += [
            "## 失败",
            "",
            _md_table(["代码", "名称", "原因"], bundle.fund_fails),
            "",
        ]
    return lines


def _overview_table_rows(bundle: WatchlistBundle) -> tuple[list[list[str]], list[dict]]:
    from tech_analysis.report import MA_TYPES, MACD_CROSS_TYPES, _latest_text

    md_rows: list[list[str]] = []
    csv_rows: list[dict] = []
    for row in bundle.rows:
        tech_ok = bool(getattr(row.tech, "ok", False))
        last_date = str(getattr(row.tech, "last_date", "") or "")
        last_close = getattr(row.tech, "last_close", None)
        signals = list(getattr(row.tech, "signals", None) or [])
        period = str((row.finance or {}).get("报告期") or "")
        status = "成功" if tech_ok or row.finance_ok else "失败"
        combined = f"[综合分析]({STOCKS_SUBDIR}/{row.folder}/{COMBINED_MD})"
        md_rows.append(
            [
                row.code,
                row.name,
                status,
                last_date or "—",
                f"{last_close:.2f}" if last_close is not None else "—",
                period or "—",
                _fmt_pct((row.finance or {}).get("营业收入同比(%)")) if row.finance else "—",
                _fmt_pct((row.finance or {}).get("归母净利润同比(%)")) if row.finance else "—",
                _latest_text(signals, MA_TYPES) if tech_ok else "—",
                _latest_text(signals, MACD_CROSS_TYPES) if tech_ok else "—",
                combined,
            ]
        )
        csv_rows.append(
            {
                "代码": row.code,
                "名称": row.name,
                "状态": status,
                "最新交易日": last_date,
                "收盘": last_close if last_close is not None else "",
                "报告期": period,
                "营收同比": (row.finance or {}).get("营业收入同比(%)", ""),
                "净利同比": (row.finance or {}).get("归母净利润同比(%)", ""),
                "综合分析": f"{STOCKS_SUBDIR}/{row.folder}/{COMBINED_MD}",
            }
        )
    return md_rows, csv_rows


def write_empty_watchlist_tree(watch_root: Path, *, stamp: str, wl_path: Path) -> Path:
    root = _reset_dir(Path(watch_root))
    (root / STOCKS_SUBDIR).mkdir(parents=True, exist_ok=True)
    empty_note = [
        f"- 生成时间：{stamp}",
        f"- 自选股文件：`{wl_path}`",
        "",
        EMPTY_MARK,
        "",
        "自选股名单为空，已跳过技术面、基本面与综合分析。",
        "点金术不读本名单当股票池；若本次是日报，点金术仍按全市场执行。",
        "",
    ]
    (root / "总表.md").write_text(
        "\n".join(["# 自选股总表", ""] + empty_note),
        encoding="utf-8",
    )
    pd.DataFrame(columns=["代码", "名称", "状态", "最新交易日", "收盘", "报告期", "营收同比", "净利同比", "综合分析"]).to_csv(
        root / "总表.csv", index=False, encoding="utf-8-sig"
    )
    (root / "技术面汇总.md").write_text(
        "\n".join(["# 自选股技术面汇总", ""] + empty_note),
        encoding="utf-8",
    )
    (root / "基本面汇总.md").write_text(
        "\n".join(["# 自选股基本面汇总", ""] + empty_note),
        encoding="utf-8",
    )
    return root


def write_watchlist_tree(watch_root: Path, bundle: WatchlistBundle) -> Path:
    """把一次分析结果落到 `自选股/` 目录树（可写多份副本）。"""
    from stock_screener.finance import FINANCE_BUNDLE_FILES

    if bundle.empty:
        return write_empty_watchlist_tree(watch_root, stamp=bundle.stamp, wl_path=bundle.wl_path)

    root = _reset_dir(Path(watch_root))
    stocks = root / STOCKS_SUBDIR
    stocks.mkdir(parents=True, exist_ok=True)

    for row in bundle.rows:
        stock_dir = stocks / row.folder
        tech_dir = stock_dir / TECH_SUBDIR
        fund_dir = stock_dir / FUND_SUBDIR
        tech_copied = _copy_named(row.tech_src, tech_dir, TECH_BUNDLE) if row.tech_src else []
        fund_copied = (
            _copy_named(row.fund_src, fund_dir, FINANCE_BUNDLE_FILES) if row.fund_src else []
        )
        write_combined_analysis(
            stock_dir / COMBINED_MD,
            code=row.code,
            name=row.name,
            stamp=bundle.stamp,
            tech=row.tech,
            finance=row.finance,
            finance_ok=row.finance_ok,
            finance_error=row.finance_error,
            fund_md_exists="财务分析.md" in fund_copied,
            tech_md_exists="分析报告.md" in tech_copied,
        )

    md_rows, csv_rows = _overview_table_rows(bundle)
    header = [
        "# 自选股总表",
        "",
        f"- 生成时间：{bundle.stamp}",
        f"- 自选股文件：`{bundle.wl_path}`",
        f"- 共 {len(bundle.items)} 只（技术面成功 {len(bundle.tech_ok)}，基本面有数据 {sum(1 for r in bundle.rows if r.finance_ok)}）",
        "- 本目录是自选股分析，与点金术 / 点金术extra **分开存放**。",
        "",
        "## 名单",
        "",
        _md_table(
            ["代码", "名称", "状态", "交易日", "收盘", "报告期", "营收同比", "净利同比", "最近MA", "最近MACD", "综合"],
            md_rows,
        ),
        "",
        f"个股在 `{STOCKS_SUBDIR}/<代码_名称>/`，内含 `{COMBINED_MD}`、`{TECH_SUBDIR}/`、`{FUND_SUBDIR}/`。",
        "",
    ]
    (root / "总表.md").write_text("\n".join(header), encoding="utf-8")
    pd.DataFrame(csv_rows).to_csv(root / "总表.csv", index=False, encoding="utf-8-sig")
    (root / "技术面汇总.md").write_text("\n".join(_tech_summary_lines(bundle)), encoding="utf-8")
    (root / "基本面汇总.md").write_text("\n".join(_fund_summary_lines(bundle)), encoding="utf-8")
    return root


def analyze_watchlist_items(
    items,
    *,
    tech_cfg: dict,
    growth_cfg: dict,
    limiter,
    tech_out_root: Path,
) -> tuple[list[WatchlistStockRow], list[list[str]], list[str]]:
    from stock_screener.finance import FINANCE_BUNDLE_FILES, analyze_stock_finance, finance_out_dir
    from tech_analysis.main import analyze_one
    from tech_analysis.market import remember_name, resolve_name
    from tech_analysis.report import StockResult, stock_dir_name

    rows: list[WatchlistStockRow] = []
    fund_fails: list[list[str]] = []
    new_filings: list[str] = []

    for item in items:
        if item.name:
            remember_name(item.code, item.name)
    for i, item in enumerate(items, 1):
        try:
            name = resolve_name(item.code, item.name, tech_cfg, limiter)
        except Exception:
            name = item.name or item.code
        folder = stock_dir_name(item.code, name)
        log.info("[%d/%d] 自选股 技术面 %s %s", i, len(items), item.code, name)
        try:
            result = analyze_one(item.code, name, tech_cfg, limiter, tech_out_root)
        except Exception as exc:
            log.error("技术面失败 %s：%s", item.code, exc)
            result = StockResult(code=item.code, name=name, ok=False, error=str(exc))

        dest = _stock_docs_dir(item.code, name)
        csv_path = dest / "财务分析" / "财务数据.csv"
        old_period = str(latest_finance_row(csv_path).get("报告期") or "")
        fetched = False
        finance_error = ""
        log.info("  基本面：按本地/远端报告期决定是否拉取 %s", item.code)
        try:
            fetched = bool(analyze_stock_finance(item.code, name, dest, growth_cfg))
        except Exception as exc:
            finance_error = str(exc)
            log.error("  财务失败 %s：%s", item.code, exc)
            fund_fails.append([item.code, name, finance_error])

        src_fin = finance_out_dir(dest)
        latest = latest_finance_row(csv_path)
        finance_ok = bool(latest) and any((src_fin / name).is_file() for name in FINANCE_BUNDLE_FILES)
        if not latest and not finance_error:
            finance_error = "无财务数据"
            if [item.code, name] not in [[a, b] for a, b, _ in fund_fails]:
                fund_fails.append([item.code, name, finance_error])
        period = str(latest.get("报告期") or "")
        is_new = bool(fetched and period and period != old_period)
        if is_new:
            new_filings.append(f"{item.code} {name} {period}")

        rows.append(
            WatchlistStockRow(
                code=item.code,
                name=name,
                folder=folder,
                tech=result,
                finance=latest,
                finance_ok=finance_ok,
                finance_error=finance_error,
                fetched_new=is_new,
                old_period=old_period,
                tech_src=result.out_dir if getattr(result, "out_dir", None) else None,
                fund_src=src_fin if src_fin.is_dir() else None,
            )
        )
    return rows, fund_fails, new_filings


def default_watchlist_roots(settings=None, today: str | None = None) -> list[Path]:
    day = today or datetime.now().strftime("%Y%m%d")
    current = settings if settings is not None else load_settings()
    return [
        watchlist_output_dir(),
        dated_report_dir(day, current) / WATCHLIST_DIR_NAME,
    ]


def run_watchlist_full_analysis(
    *,
    watch_roots: list[Path] | None = None,
    stamp: str | None = None,
    settings=None,
) -> WatchlistBundle:
    """分析全部自选股并写入各 `watch_roots`（默认 data/watchlist + 今日日报集/自选股）。"""
    from stock_screener.config import load_config as load_growth_cfg
    from tech_analysis.config import load_config as load_tech_cfg
    from tech_analysis.network import RateLimiter, disable_proxies, enable_browser_tls
    from tech_analysis.report import write_summary
    from tech_analysis.watchlist import load_watchlist

    chdir_project_root()
    current = settings if settings is not None else load_settings()
    wl = ensure_watchlist()
    items = load_watchlist(wl)
    empty = not items
    now_stamp = stamp or datetime.now().strftime("%Y-%m-%d %H:%M")
    earnings = current.is_earnings_season()
    roots = list(watch_roots) if watch_roots is not None else default_watchlist_roots(current)
    unique_roots: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        path = Path(root)
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique_roots.append(path)

    if empty:
        log.warning("自选股为空：%s。自选股目录记「%s」。", wl, EMPTY_MARK)
        bundle = WatchlistBundle(
            empty=True,
            items=[],
            rows=[],
            wl_path=wl,
            stamp=now_stamp,
            earnings=earnings,
            roots=unique_roots,
        )
        for root in unique_roots:
            write_empty_watchlist_tree(root, stamp=now_stamp, wl_path=wl)
            prune_legacy_date_root_watchlist(root.parent)
        return bundle

    disable_proxies()
    enable_browser_tls()
    tech_cfg = load_tech_cfg()
    growth_cfg = load_growth_cfg()
    limiter = RateLimiter(1.5)
    out_root = tech_output_dir()
    out_root.mkdir(parents=True, exist_ok=True)

    rows, fund_fails, new_filings = analyze_watchlist_items(
        items,
        tech_cfg=tech_cfg,
        growth_cfg=growth_cfg,
        limiter=limiter,
        tech_out_root=out_root,
    )
    write_summary([r.tech for r in rows], out_root / "信号汇总.md", out_root / "信号汇总.csv")
    bundle = WatchlistBundle(
        empty=False,
        items=items,
        rows=rows,
        wl_path=wl,
        stamp=now_stamp,
        earnings=earnings,
        fund_fails=fund_fails,
        new_filings=new_filings,
        roots=unique_roots,
    )
    for root in unique_roots:
        write_watchlist_tree(root, bundle)
        prune_legacy_date_root_watchlist(root.parent)
        log.info("自选股分析已写入：%s", root)
    return bundle


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    del args  # 一键分析不读 daily_update；用户显式触发即运行
    bundle = run_watchlist_full_analysis()
    if bundle.empty:
        log.info("自选股名单为空，已写「%s」", EMPTY_MARK)
        return 0
    ok_n = len(bundle.tech_ok)
    log.info("自选股分析完成：技术面成功 %d / %d", ok_n, len(bundle.rows))
    return 0
