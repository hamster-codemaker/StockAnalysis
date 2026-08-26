"""A股成长股文档自动下载器 —— 命令行入口。

用法：
  python main.py screen                     仅执行量化筛选，输出清单
  python main.py download [--codes ...]     仅下载文档（默认读取最新筛选结果）
  python main.py policies [--industries ..] 仅下载行业政策文件库与个股政策索引
  python main.py update-policies            定期刷新政策库：只入库总清单没有的新文件
  python main.py finance [--codes ...]      仅提取历年财报关键数据并出图
  python main.py run [--limit N]            筛选 + 下载 全流程
  python main.py dianjin                    点金术 / extra 全市场筛选（日报同路径，无 hist-limit）

数据源均为合法公开渠道：巨潮资讯网（招股书/财报）、东方财富研报中心（研报）、
国务院政策文件库（行业政策）、东方财富 F10 公开财报（财务分析）。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from stock_screener.config import cfg_get, load_config
from stock_screener.datasources import cninfo as cninfo_mod
from stock_screener.datasources import eastmoney as em_mod
from stock_screener.datasources.cninfo import CninfoClient
from stock_screener.datasources.eastmoney import EastmoneyClient
from stock_screener.datasources.govpolicy import GovPolicyClient, clean_industry_keyword
from stock_screener.downloader import HttpClient, sanitize_filename
from stock_screener.finance import analyze_stock_finance
from stock_screener.screener import run_screening

log = logging.getLogger("main")


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# ---------- 筛选结果输出 ----------

def df_to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "（无符合条件的股票）"
    header = "| " + " | ".join(str(c) for c in df.columns) + " |"
    sep = "|" + " --- |" * len(df.columns)
    body = [
        "| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |"
        for row in df.itertuples(index=False)
    ]
    return "\n".join([header, sep, *body])


def save_screening(df: pd.DataFrame, cfg: dict) -> Path:
    out_dir = Path(cfg_get(cfg, "paths.screening_dir", "data/screening"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    csv_path = out_dir / f"成长股筛选_{stamp}.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    include_bse = cfg_get(cfg, "screening.include_bse", False)
    md_lines = [
        f"# 成长股筛选报告 {datetime.now():%Y-%m-%d %H:%M}",
        "",
        "筛选条件（纯量化）：",
        f"- ④ 总市值 < {cfg_get(cfg, 'screening.market_cap_max_yi')} 亿元，"
        f"剔除ST/退市整理股{'' if include_bse else '，不含北交所'}",
        f"- ① 最新 {cfg_get(cfg, 'screening.growth.consecutive_periods')} 期报告："
        f"营收同比 >= {cfg_get(cfg, 'screening.growth.revenue_yoy_min')}% "
        f"且净利润同比 >= {cfg_get(cfg, 'screening.growth.profit_yoy_min')}%",
        f"- ⑤ 机构持有家数 >= {cfg_get(cfg, 'screening.institution.min_institutions')} "
        f"且占流通股比例 <= {cfg_get(cfg, 'screening.institution.max_float_ratio_pct')}%",
        "",
        "说明：② 估值、③ 增长动因、⑥ 产业趋势不在程序判断范围内，请结合下载的文档人工分析。",
        "",
        f"共 {len(df)} 只入选。",
        "",
        df_to_markdown(df),
        "",
    ]
    md_path = out_dir / f"成长股筛选_{stamp}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    log.info("筛选结果已保存：%s 与 %s", csv_path, md_path)
    return csv_path


# ---------- 下载 ----------

def build_clients(cfg: dict) -> tuple[HttpClient, CninfoClient, EastmoneyClient, GovPolicyClient]:
    http = HttpClient(
        rate_limit_seconds=cfg_get(cfg, "network.rate_limit_seconds", 1.5),
        max_retries=cfg_get(cfg, "network.max_retries", 3),
        timeout_seconds=cfg_get(cfg, "network.timeout_seconds", 30),
        manifest_path=cfg_get(cfg, "paths.manifest", "data/manifest.json"),
        use_system_proxy=cfg_get(cfg, "network.use_system_proxy", False),
    )
    cn = CninfoClient(http, cache_dir=cfg_get(cfg, "paths.cache_dir", "data/cache"))
    em = EastmoneyClient(http)
    gov = GovPolicyClient(http)
    return http, cn, em, gov


def _stock_dir(cfg: dict, code: str, name: str) -> Path:
    base = Path(cfg_get(cfg, "downloads.output_dir", "data/docs"))
    return base / f"{code}_{sanitize_filename(name, 20)}"


def download_stock_documents(
    code: str,
    name: str,
    cfg: dict,
    http: HttpClient,
    cn: CninfoClient,
    em: EastmoneyClient,
) -> tuple[dict, str]:
    """下载单只股票的招股书/财报/研报，返回（统计, 发现的所属行业名）。"""
    stock_dir = _stock_dir(cfg, code, name)
    stats = {"ok": 0, "skip": 0, "fail": 0}
    industry_name = ""

    def grab(url: str, dest: Path, referer: str, impersonate: bool = False) -> None:
        status = http.download_pdf(url, dest, referer=referer, impersonate=impersonate)
        stats[status] += 1
        if status == "ok":
            log.info("    已下载：%s", dest.name)
        elif status == "fail":
            log.warning("    下载失败：%s", url)

    # 招股说明书
    if cfg_get(cfg, "downloads.prospectus.enabled", True):
        items = cn.prospectus(code)
        if not items:
            log.info("  未找到招股说明书（早年上市的公司可能未在巨潮电子化披露）")
        for it in items:
            dest = stock_dir / "招股说明书" / f"{it['date']}_{sanitize_filename(it['title'])}.pdf"
            grab(it["url"], dest, cninfo_mod.REFERER)

    # 定期财报（全量）
    if cfg_get(cfg, "downloads.financial_reports.enabled", True):
        types = cfg_get(cfg, "downloads.financial_reports.types", ["年报"])
        since = cfg_get(cfg, "downloads.financial_reports.since", "1990-01-01")
        reports = cn.periodic_reports(code, types, since)
        log.info("  定期报告共 %d 份", len(reports))
        for it in reports:
            dest = stock_dir / "财报" / f"{it['date']}_{sanitize_filename(it['title'])}.pdf"
            grab(it["url"], dest, cninfo_mod.REFERER)

    # 研报（个股 + 行业，额外参考材料）
    if cfg_get(cfg, "downloads.research_reports.enabled", True):
        meta_rows: list[dict] = []
        s_months = int(cfg_get(cfg, "downloads.research_reports.stock.months", 6))
        s_max = int(cfg_get(cfg, "downloads.research_reports.stock.max_count", 10))
        ratings = cfg_get(cfg, "downloads.research_reports.stock.ratings", [])
        selected, ind_code, ind_name = em.stock_reports(
            code, months=s_months, ratings=ratings, max_count=s_max
        )
        industry_name = ind_name or ""
        log.info("  个股研报 %d 篇（评级过滤：%s）", len(selected), "、".join(ratings) or "无")
        for it in selected:
            fname = (
                f"{it['date']}_{sanitize_filename(it['org'], 20)}_"
                f"{sanitize_filename(it['title'], 60)}.pdf"
            )
            grab(em.pdf_url(it["info_code"]), stock_dir / "研报" / fname,
                 em_mod.REFERER, impersonate=True)
            meta_rows.append(_report_meta(it, "个股", fname))

        if cfg_get(cfg, "downloads.research_reports.industry.enabled", True) and ind_code:
            i_months = int(cfg_get(cfg, "downloads.research_reports.industry.months", 3))
            i_max = int(cfg_get(cfg, "downloads.research_reports.industry.max_count", 5))
            industry_items = em.industry_reports(ind_code, months=i_months, max_count=i_max)
            log.info("  行业研报 %d 篇（%s）", len(industry_items), ind_name or ind_code)
            for it in industry_items:
                fname = (
                    f"{it['date']}_行业_{sanitize_filename(it['org'], 20)}_"
                    f"{sanitize_filename(it['title'], 60)}.pdf"
                )
                grab(em.pdf_url(it["info_code"]), stock_dir / "研报" / fname,
                     em_mod.REFERER, impersonate=True)
                meta_rows.append(_report_meta(it, "行业", fname))

        if meta_rows:
            meta_path = stock_dir / "研报" / "研报清单.csv"
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(meta_rows).to_csv(meta_path, index=False, encoding="utf-8-sig")
    return stats, industry_name


def _report_meta(item: dict, kind: str, filename: str) -> dict:
    return {
        "日期": item["date"],
        "类型": kind,
        "标题": item["title"],
        "机构": item["org"],
        "评级": item["rating"],
        "预测今年EPS": item["eps_this"],
        "预测明年EPS": item["eps_next"],
        "预测今年PE": item["pe_this"],
        "预测明年PE": item["pe_next"],
        "文件名": filename,
    }


def _default_watchlist_file() -> Path:
    try:
        from launcher.paths import ensure_watchlist

        return ensure_watchlist()
    except Exception:
        return Path("userdata/watchlist.txt")


def _screening_industry_map(cfg: dict) -> dict[str, tuple[str, str]]:
    """最新筛选 CSV：代码 -> (名称, 所处行业)。仅作行业提示，不单独建目录。"""
    directory = Path(cfg_get(cfg, "paths.screening_dir", "data/screening"))
    files = sorted(directory.glob("成长股筛选_*.csv"))
    if not files or not files[-1].exists():
        return {}
    try:
        df = pd.read_csv(files[-1], dtype=str)
    except Exception as exc:
        log.warning("筛选结果读取失败：%s（%s）", files[-1], exc)
        return {}
    names = df["名称"] if "名称" in df.columns else [""] * len(df)
    industries = df["所处行业"] if "所处行业" in df.columns else [""] * len(df)
    mapping: dict[str, tuple[str, str]] = {}
    for code, name, industry in zip(df.get("代码", []), names, industries):
        mapping[str(code).zfill(6)] = (
            "" if pd.isna(name) else str(name),
            "" if pd.isna(industry) else str(industry),
        )
    return mapping


def load_watchlist_stocks(path: str | Path, cfg: dict, cn: CninfoClient | None = None) -> list[tuple[str, str, str]]:
    file_path = Path(path)
    if not file_path.is_file():
        log.error("自选股文件不存在：%s", file_path)
        return []
    try:
        from tech_analysis.watchlist import load_watchlist

        items = load_watchlist(file_path)
    except Exception as exc:
        log.error("读取自选股失败：%s（%s）", file_path, exc)
        return []
    screening = _screening_industry_map(cfg)
    out: list[tuple[str, str, str]] = []
    for item in items:
        name = item.name or screening.get(item.code, ("", ""))[0]
        if not name and cn is not None:
            name = cn.stock_name(item.code) or item.code
        industry = screening.get(item.code, ("", ""))[1]
        out.append((item.code, name or item.code, industry))
    log.info("读取自选股 %d 只：%s", len(out), file_path)
    return out


def resolve_stocks(
    args: argparse.Namespace, cfg: dict, cn: CninfoClient
) -> list[tuple[str, str, str]]:
    """解析待处理股票，返回 [(代码, 名称, 所处行业)]；行业可能为空串。

    优先级：--codes > --watchlist > --screening-file > 最新筛选 CSV。
    自选股与机械选股目录互不混用。
    """
    codes_arg = getattr(args, "codes", None)
    if codes_arg:
        codes = [c.strip().zfill(6) for c in codes_arg.split(",") if c.strip()]
        return [(c, cn.stock_name(c) or c, "") for c in codes]
    watchlist_arg = getattr(args, "watchlist", None)
    if watchlist_arg:
        return load_watchlist_stocks(watchlist_arg, cfg, cn)
    file_arg = getattr(args, "screening_file", None)
    if file_arg:
        files = [Path(file_arg)]
    else:
        directory = Path(cfg_get(cfg, "paths.screening_dir", "data/screening"))
        files = sorted(directory.glob("成长股筛选_*.csv"))
    if not files or not files[-1].exists():
        return []
    latest = files[-1]
    log.info("读取筛选结果：%s", latest)
    df = pd.read_csv(latest, dtype=str)
    industries = df["所处行业"] if "所处行业" in df.columns else [""] * len(df)
    return [
        (str(c).zfill(6), str(n), "" if pd.isna(i) else str(i))
        for c, n, i in zip(df["代码"], df["名称"], industries)
    ]


def cmd_download(cfg: dict, args: argparse.Namespace, stocks=None) -> int:
    http, cn, em, gov = build_clients(cfg)
    if stocks is None:
        stocks = resolve_stocks(args, cfg, cn)
    if not stocks:
        log.error("没有待下载的股票：请先运行 `python main.py screen`，或用 --codes / --watchlist 指定")
        return 1
    limit = getattr(args, "limit", None)
    if limit:
        stocks = stocks[:limit]
    total = {"ok": 0, "skip": 0, "fail": 0}
    stock_dirs: list[tuple[Path, str]] = []  # (股票目录, 所处行业)
    finance_enabled = bool(cfg_get(cfg, "downloads.financial_analysis.enabled", True))
    for i, (code, name, industry_hint) in enumerate(stocks, 1):
        log.info("[%d/%d] %s %s", i, len(stocks), code, name)
        stats, discovered = download_stock_documents(code, name, cfg, http, cn, em)
        dest = _stock_dir(cfg, code, name)
        stock_dirs.append((dest, industry_hint or discovered))
        for key in total:
            total[key] += stats[key]
        if finance_enabled:
            analyze_stock_finance(code, name, dest, cfg)
    log.info(
        "个股文档完成：新下载 %d，跳过(已存在) %d，失败 %d。文件目录：%s",
        total["ok"], total["skip"], total["fail"],
        Path(cfg_get(cfg, "downloads.output_dir", "data/docs")).resolve(),
    )
    if cfg_get(cfg, "downloads.policies.enabled", True):
        industries = {ind for _, ind in stock_dirs if ind}
        results = download_industry_policies(industries, cfg, http, gov)
        write_policy_indexes(stock_dirs, _enrich_policy_results(cfg, results), cfg)
    return 0


# ---------- 行业政策文件 ----------
#
# 存储布局：所有政策正文(.md)与PDF附件统一存入中央文件库 <policies.output_dir>/文件库，
# 跨行业、跨股票全局去重；<policies.output_dir>/政策总清单.csv 记录全库清单（按原文链接
# 合并，行业关键词累积）；每只个股目录下只生成 政策文件索引.md 引用文件库，不复制文件。

_CATALOG_COLUMNS = [
    "日期", "来源", "发文机关", "发文字号", "主题分类", "标题", "关键词",
    "文件名", "原文链接", "入库日期",
]


def _policy_root(cfg: dict) -> Path:
    return Path(cfg_get(cfg, "downloads.policies.output_dir", "data/policies"))


def _policy_library(cfg: dict) -> Path:
    return _policy_root(cfg) / "文件库"


def _merge_keywords(existing: str, new: str) -> str:
    """合并关键词列（分号分隔），保持顺序去重。"""
    merged: list[str] = []
    for part in f"{existing};{new}".split(";"):
        part = part.strip()
        if part and part not in merged:
            merged.append(part)
    return ";".join(merged)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _fill_ingest_date(row: dict, library: Path) -> str:
    """已有入库日期不覆盖；缺失则用正文文件修改日，再退回今天。"""
    existing = str(row.get("入库日期", "") or "").strip()
    if existing:
        return existing
    fname = str(row.get("文件名", "") or "").strip()
    path = library / fname if fname else None
    if path is not None and path.exists():
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    return _today()


def _load_policy_catalog(path: Path) -> dict[str, dict]:
    """读取政策总清单CSV，返回 原文链接 -> 行dict；文件不存在或损坏时返回空。"""
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception as exc:
        log.warning("政策总清单读取失败，将重建：%s（%s）", path, exc)
        return {}
    catalog: dict[str, dict] = {}
    for row in df.to_dict("records"):
        url = str(row.get("原文链接", "")).strip()
        if url:
            catalog[url] = {col: str(row.get(col, "") or "") for col in _CATALOG_COLUMNS}
    return catalog


def _save_policy_catalog(path: Path, catalog: dict[str, dict], library: Path) -> None:
    rows = []
    for row in catalog.values():
        filled = {col: str(row.get(col, "") or "") for col in _CATALOG_COLUMNS}
        filled["入库日期"] = _fill_ingest_date(filled, library)
        rows.append(filled)
    rows.sort(key=lambda r: (str(r.get("日期", "")), str(r.get("标题", ""))), reverse=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=_CATALOG_COLUMNS).to_csv(
        path, index=False, encoding="utf-8-sig"
    )


def _policy_markdown(item: dict, text: str, attachments: list[str]) -> str:
    lines = [
        f"# {item['title']}",
        "",
        f"- 来源：{item['source']}（国务院政策文件库 www.gov.cn）",
        f"- 发文机关：{item['org'] or '—'}",
        f"- 发文字号：{item['pcode'] or '—'}",
        f"- 发布日期：{item['date'] or '—'}",
        f"- 主题分类：{item['theme'] or '—'}",
        f"- 原文链接：{item['url']}",
        "",
        "---",
        "",
        text,
    ]
    if attachments:
        lines += ["", "---", "", "附件："]
        lines += [f"- {url}" for url in attachments]
    lines.append("")
    return "\n".join(lines)


def _catalog_row(item: dict, keyword: str, fname: str, old: dict, ingest_date: str) -> dict:
    return {
        "日期": item["date"],
        "来源": item["source"],
        "发文机关": item["org"],
        "发文字号": item["pcode"],
        "主题分类": item["theme"],
        "标题": item["title"],
        "关键词": _merge_keywords(old.get("关键词", ""), keyword),
        "文件名": fname,
        "原文链接": item["url"],
        "入库日期": ingest_date,
    }


def download_industry_policies(
    industries: set[str],
    cfg: dict,
    http: HttpClient,
    gov: GovPolicyClient,
    update_mode: bool = False,
) -> dict[str, list[dict]]:
    """按行业检索政策并统一存入中央文件库（以原文链接为唯一键），同时维护政策总清单。

    总清单已有的条目不重抓正文/附件；仅入库新文件。已有行的「入库日期」不覆盖。
    返回 行业关键词 -> 条目列表（每条为检索条目dict外加"文件名"键），供生成个股政策索引。
    """
    results: dict[str, list[dict]] = {}
    keywords = sorted({clean_industry_keyword(i) for i in industries if str(i).strip()})
    if not keywords:
        log.info("无可用行业关键词，跳过政策文件下载")
        return results
    per_max = int(cfg_get(cfg, "downloads.policies.per_industry_max", 30))
    supplement = bool(cfg_get(cfg, "downloads.policies.fulltext_supplement", True))
    library = _policy_library(cfg)
    catalog_path = _policy_root(cfg) / "政策总清单.csv"
    catalog = _load_policy_catalog(catalog_path)
    stats = {"ok": 0, "skip": 0, "fail": 0}
    action = "政策库更新" if update_mode else "行业政策文件"
    log.info("%s：%d 个行业（%s），每行业最多 %d 篇；只入库总清单没有的新文件",
             action, len(keywords), "、".join(keywords), per_max)
    for keyword in keywords:
        entries: list[dict] = []
        kw_stats = {"ok": 0, "skip": 0, "fail": 0}
        for it in gov.search(keyword, max_count=per_max, fulltext_supplement=supplement):
            url = it["url"]
            old = catalog.get(url) or {}
            fname_base = f"{it['date']}_{sanitize_filename(it['title'], 60)}"
            fname = old.get("文件名") or f"{fname_base}.md"
            md_path = library / fname
            in_catalog = url in catalog
            md_exists = md_path.exists()

            if in_catalog and md_exists:
                kw_stats["skip"] += 1
                stats["skip"] += 1
                catalog[url] = _catalog_row(
                    it, keyword, fname, old, _fill_ingest_date(old, library)
                )
                entries.append({**it, "文件名": fname})
                continue
            if md_exists and not in_catalog:
                kw_stats["skip"] += 1
                stats["skip"] += 1
                catalog[url] = _catalog_row(
                    it, keyword, fname, old, _fill_ingest_date({"文件名": fname}, library)
                )
                entries.append({**it, "文件名": fname})
                continue

            text, attachments = gov.fetch_detail(url)
            if not text:
                kw_stats["fail"] += 1
                stats["fail"] += 1
                log.warning("    正文抓取失败：%s", url)
                continue
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(_policy_markdown(it, text, attachments), encoding="utf-8")
            kw_stats["ok"] += 1
            stats["ok"] += 1
            log.info("    已入库：%s", fname)
            for idx, att in enumerate(attachments, 1):
                if att.lower().split("?")[0].endswith(".pdf"):
                    http.download_pdf(
                        att, library / f"{Path(fname).stem}_附件{idx}.pdf", referer=url
                    )
            ingest = old.get("入库日期") if in_catalog else _today()
            catalog[url] = _catalog_row(it, keyword, fname, old, ingest or _today())
            entries.append({**it, "文件名": fname})
        log.info(
            "  「%s」：检索后新增 %d 篇，跳过已有 %d 篇，失败 %d 篇",
            keyword, kw_stats["ok"], kw_stats["skip"], kw_stats["fail"],
        )
        if not entries:
            log.info("  「%s」未检索到政策文件", keyword)
        results[keyword] = entries
    if catalog:
        _save_policy_catalog(catalog_path, catalog, library)
    log.info(
        "%s完成：新增 %d 篇，跳过已有 %d 篇，失败 %d 篇；涉及行业：%s。文件库：%s；总清单：%s",
        action, stats["ok"], stats["skip"], stats["fail"],
        "、".join(keywords), library.resolve(), catalog_path,
    )
    return results


def write_stock_policy_index(
    stock_dir: Path, industry: str, keyword: str, entries: list[dict], library: Path
) -> Path:
    """在个股目录生成 政策文件索引.md：仅链接中央文件库中的文件，不复制本体。"""
    stock_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 行业政策文件索引：{keyword}",
        "",
        f"- 所处行业：{industry}（检索关键词：{keyword}）",
        f"- 共 {len(entries)} 篇。政策正文与附件统一存放于共享政策文件库，本索引仅作链接，不复制文件。",
        f"- 更新时间：{datetime.now():%Y-%m-%d %H:%M}",
        "",
        "| 日期 | 发文机关 | 标题（本地文件） | 原文 |",
        "| --- | --- | --- | --- |",
    ]
    for it in sorted(entries, key=lambda x: str(x.get("date", "")), reverse=True):
        rel = os.path.relpath(library / it["文件名"], stock_dir).replace(os.sep, "/")
        title = str(it["title"]).replace("|", "｜")
        org = str(it["org"] or "—").replace("|", "｜")
        lines.append(
            f"| {it['date']} | {org} | [{title}](<{rel}>) | [gov.cn]({it['url']}) |"
        )
    lines.append("")
    path = stock_dir / "政策文件索引.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _entries_from_catalog(cfg: dict, keyword: str) -> list[dict]:
    """用总清单里带该行业关键词的已入库文件补全个股索引，避免 --max 过小把旧索引截断。"""
    catalog = _load_policy_catalog(_policy_root(cfg) / "政策总清单.csv")
    out: list[dict] = []
    for row in catalog.values():
        parts = [p.strip() for p in str(row.get("关键词") or "").split(";") if p.strip()]
        if keyword not in parts and not any(keyword in p or p in keyword for p in parts):
            continue
        fname = str(row.get("文件名") or "").strip()
        url = str(row.get("原文链接") or "").strip()
        if not fname or not url:
            continue
        out.append(
            {
                "date": row.get("日期", ""),
                "org": row.get("发文机关", ""),
                "title": row.get("标题", ""),
                "url": url,
                "文件名": fname,
            }
        )
    return out


def _enrich_policy_results(cfg: dict, results: dict[str, list[dict]]) -> dict[str, list[dict]]:
    enriched: dict[str, list[dict]] = {}
    for keyword, entries in results.items():
        by_url: dict[str, dict] = {}
        for item in _entries_from_catalog(cfg, keyword):
            by_url[item["url"]] = item
        for item in entries:
            url = str(item.get("url") or "")
            if url:
                by_url[url] = item
        enriched[keyword] = list(by_url.values())
        log.info("  索引条目「%s」：本次检索 %d，合并总清单后 %d", keyword, len(entries), len(enriched[keyword]))
    return enriched


def write_policy_indexes(
    stock_dirs: list[tuple[Path, str]], results: dict[str, list[dict]], cfg: dict
) -> None:
    """为各股票目录生成政策文件索引；stock_dirs 为 (股票目录, 所处行业) 列表。"""
    library = _policy_library(cfg)
    for stock_dir, industry in stock_dirs:
        if not str(industry).strip():
            log.info("  %s：未识别所处行业，跳过政策索引", stock_dir.name)
            continue
        keyword = clean_industry_keyword(industry)
        entries = results.get(keyword) or []
        if not entries:
            log.info("  %s：行业「%s」暂无政策条目，跳过政策索引", stock_dir.name, keyword)
            continue
        path = write_stock_policy_index(stock_dir, industry, keyword, entries, library)
        log.info("  政策索引已生成：%s（%d 条）", path, len(entries))


def _has_local_reports(stock_dir: Path) -> bool:
    """已下载过财报或研报（目录内有文件，含研报清单.csv）。"""
    for sub in ("财报", "研报"):
        folder = stock_dir / sub
        if not folder.is_dir():
            continue
        try:
            if any(p.is_file() for p in folder.iterdir()):
                return True
        except OSError:
            continue
    return False


def _folder_code(folder: Path) -> str:
    name = folder.name
    return name[:6] if len(name) >= 6 and name[:6].isdigit() else ""


def _industry_from_docs(folder: Path) -> str:
    index_path = folder / "政策文件索引.md"
    if index_path.is_file():
        try:
            text = index_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        marker = "所处行业："
        if marker in text:
            tail = text.split(marker, 1)[1].split("\n", 1)[0]
            return tail.split("（")[0].strip()
        if "行业政策文件索引：" in text:
            return text.split("行业政策文件索引：", 1)[1].split("\n", 1)[0].strip()
    meta = folder / "研报" / "研报清单.csv"
    if meta.is_file():
        try:
            df = pd.read_csv(meta, dtype=str, encoding="utf-8-sig")
            for col in ("所处行业", "行业"):
                if col in df.columns:
                    values = [str(v).strip() for v in df[col] if str(v).strip() and str(v) != "nan"]
                    if values:
                        return values[0]
        except Exception:
            pass
    return ""


def _industry_matches(industry: str, keywords: set[str]) -> bool:
    cleaned = clean_industry_keyword(industry)
    if not cleaned or not keywords:
        return False
    if cleaned in keywords:
        return True
    return any(k and (k in cleaned or cleaned in k) for k in keywords)


def _find_docs_folder(docs: Path, code: str) -> Path | None:
    if not docs.exists():
        return None
    matches = sorted(p for p in docs.iterdir() if p.is_dir() and p.name.startswith(f"{code}_"))
    if matches:
        return matches[0]
    exact = docs / code
    return exact if exact.is_dir() else None


def _collect_stock_dirs_for_industries(
    cfg: dict,
    industries: set[str],
    stocks: list[tuple[str, str, str]],
) -> list[tuple[Path, str]]:
    """只给「自选股」和「本地已有财报/研报」的股票写政策索引，不为筛选清单建空目录。"""
    keywords = {clean_industry_keyword(i) for i in industries if str(i).strip()}
    docs = Path(cfg_get(cfg, "downloads.output_dir", "data/docs"))
    hints: dict[str, tuple[str, str]] = {c: (n, i) for c, n, i in stocks}
    hints.update(_screening_industry_map(cfg))

    watchlist_codes: set[str] = set()
    try:
        wl_stocks = load_watchlist_stocks(_default_watchlist_file(), cfg, None)
        for code, name, industry in wl_stocks:
            watchlist_codes.add(code)
            old = hints.get(code, ("", ""))
            hints[code] = (name or old[0], industry or old[1])
    except Exception as exc:
        log.warning("读取自选股以刷新政策索引时失败：%s", exc)

    eligible: dict[str, Path] = {}
    for code in watchlist_codes:
        name = hints.get(code, ("", ""))[0] or code
        folder = _find_docs_folder(docs, code) or _stock_dir(cfg, code, name)
        eligible[code] = folder

    if docs.exists():
        for folder in docs.iterdir():
            if not folder.is_dir():
                continue
            code = _folder_code(folder)
            if not code or not _has_local_reports(folder):
                continue
            eligible[code] = folder
            if code not in hints:
                hints[code] = (folder.name[7:] if len(folder.name) > 7 else code, _industry_from_docs(folder))

    for code, name, industry in stocks:
        if code in eligible:
            continue
        if _industry_matches(industry, keywords):
            log.info("  跳过 %s %s：筛选命中但本地无财报/研报，不建空目录", code, name)

    result: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for code, folder in sorted(eligible.items()):
        name, industry = hints.get(code, ("", ""))
        inferred = _industry_from_docs(folder)
        chosen = industry if _industry_matches(industry, keywords) else inferred
        if not _industry_matches(chosen, keywords):
            if code in watchlist_codes:
                log.info("  跳过自选股 %s %s：行业「%s」与本次关键词不匹配", code, name, chosen or industry or "未知")
            else:
                log.info("  跳过 %s：已有文档但行业「%s」与本次关键词不匹配", folder.name, chosen or "未知")
            continue
        key = str(folder.resolve())
        if key in seen:
            continue
        seen.add(key)
        result.append((folder, chosen or next(iter(keywords))))
        source = "自选股" if code in watchlist_codes else "已有财报/研报"
        log.info("  将刷新政策索引：%s %s（%s，行业 %s）", code, name or folder.name, source, chosen)
    return result


def cmd_policies(cfg: dict, args: argparse.Namespace, update_mode: bool = False) -> int:
    http, cn, _, gov = build_clients(cfg)
    stocks: list[tuple[str, str, str]] = []
    if getattr(args, "industries", None):
        industries = {k.strip() for k in args.industries.split(",") if k.strip()}
        stocks = resolve_stocks(args, cfg, cn)
    else:
        stocks = resolve_stocks(args, cfg, cn)
        industries = {ind for _, _, ind in stocks if ind}
        if not industries:
            log.error("未能从筛选结果获取行业：请用 --industries 指定行业或关键词（逗号分隔）")
            return 1
    if getattr(args, "max", None):
        cfg.setdefault("downloads", {}).setdefault("policies", {})["per_industry_max"] = args.max
    limit = getattr(args, "limit", None)
    if limit:
        industries = set(sorted(industries)[:limit])
    results = download_industry_policies(
        industries, cfg, http, gov, update_mode=update_mode
    )
    stock_dirs = _collect_stock_dirs_for_industries(cfg, industries, stocks)
    if stock_dirs:
        write_policy_indexes(stock_dirs, _enrich_policy_results(cfg, results), cfg)
        log.info("已刷新 %d 只相关个股的政策索引（仅自选股或已有财报/研报）", len(stock_dirs))
    else:
        log.info(
            "中央政策库已更新；没有符合条件的个股需要写索引"
            "（只更新自选股与已有财报/研报的股票，不为筛选清单建空目录）"
        )
    return 0


def cmd_dianjin(cfg: dict, args: argparse.Namespace) -> int:
    from dianjin.pipeline import run_dianjin

    codes = None
    raw = getattr(args, "codes", None)
    if raw:
        codes = [c.strip().zfill(6) for c in str(raw).replace("，", ",").split(",") if c.strip()]
    result = run_dianjin(
        cfg,
        screen_only=bool(getattr(args, "screen_only", False)),
        limit=getattr(args, "limit", None),
        hist_limit=getattr(args, "hist_limit", None),
        codes=codes,
    )
    return result.returncode


def cmd_finance(cfg: dict, args: argparse.Namespace) -> int:
    _, cn, _, _ = build_clients(cfg)
    stocks = resolve_stocks(args, cfg, cn)
    if not stocks:
        log.error("没有待分析的股票：请先运行 `python main.py screen`，或用 --codes / --watchlist 指定")
        return 1
    limit = getattr(args, "limit", None)
    if limit:
        stocks = stocks[:limit]
    ok = fail = 0
    for i, (code, name, _) in enumerate(stocks, 1):
        log.info("[%d/%d] 财务分析 %s %s", i, len(stocks), code, name)
        if analyze_stock_finance(code, name, _stock_dir(cfg, code, name), cfg):
            ok += 1
        else:
            fail += 1
    log.info("财务分析完成：成功 %d，失败 %d", ok, fail)
    return 0 if fail == 0 else 1


def _disable_proxies() -> None:
    """绕过系统/环境代理直连（数据源均为国内公开站点）。

    Windows 注册表代理仅靠 NO_PROXY 无法完全绕过，因此同时将后续创建的
    requests.Session 的 trust_env 置为 False，使 akshare 内部请求同样直连。
    """
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    orig_init = requests.Session.__init__

    def init_without_proxy(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        self.trust_env = False

    requests.Session.__init__ = init_without_proxy


# ---------- 入口 ----------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A股成长股文档自动下载器（数据源均为合法公开渠道）")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("screen", help="仅执行成长股量化筛选")

    p_dl = sub.add_parser("download", help="仅下载文档（默认读取最新筛选结果）")
    p_dl.add_argument("--codes", help="逗号分隔的股票代码，如 300750,688111")
    p_dl.add_argument("--watchlist", help="自选股 txt 路径（每行6位代码，与筛选 CSV 独立）")
    p_dl.add_argument("--screening-file", help="指定筛选结果CSV路径")
    p_dl.add_argument("--limit", type=int, help="最多处理的股票数量")

    p_pol = sub.add_parser("policies", help="仅下载行业政策文件（国务院政策文件库）")
    p_pol.add_argument("--industries", help="逗号分隔的行业名或关键词，如 集成电路,人工智能；缺省从最新筛选结果取行业")
    p_pol.add_argument("--watchlist", help="自选股 txt 路径；索引仍只写自选股与已有文档的股票")
    p_pol.add_argument("--screening-file", help="指定筛选结果CSV路径")
    p_pol.add_argument("--max", type=int, help="每个行业最多收录的政策文件数（覆盖配置）")
    p_pol.add_argument("--limit", type=int, help="最多处理的行业数量")

    p_upd = sub.add_parser("update-policies", help="定期刷新政策库：只入库总清单没有的新文件，已有条目不重抓")
    p_upd.add_argument("--industries", help="逗号分隔的行业名或关键词；缺省从最新筛选结果取行业")
    p_upd.add_argument("--watchlist", help="自选股 txt 路径；索引仍只写自选股与已有文档的股票")
    p_upd.add_argument("--screening-file", help="指定筛选结果CSV路径")
    p_upd.add_argument("--max", type=int, help="每个行业最多检索的政策文件数（覆盖配置）")
    p_upd.add_argument("--limit", type=int, help="最多处理的行业数量")

    p_fin = sub.add_parser("finance", help="从公开财报提取关键财务数据并出图（不解析扫描版 PDF）")
    p_fin.add_argument("--codes", help="逗号分隔的股票代码，如 688308,301500")
    p_fin.add_argument("--watchlist", help="自选股 txt 路径（每行6位代码，与筛选 CSV 独立）")
    p_fin.add_argument("--screening-file", help="指定筛选结果CSV路径")
    p_fin.add_argument("--limit", type=int, help="最多处理的股票数量")

    p_run = sub.add_parser("run", help="筛选 + 下载 全流程")
    p_run.add_argument("--limit", type=int, help="最多下载的股票数量")

    p_dj = sub.add_parser("dianjin", help="点金术 / 点金术extra：全市场高股息低估值折价筛选")
    p_dj.add_argument("--codes", help="仅保留这些代码（测试/手工；日报不用）")
    p_dj.add_argument("--limit", type=int, help="最多写入/增强的命中数（仅测试，日报不用）")
    p_dj.add_argument(
        "--hist-limit",
        type=int,
        dest="hist_limit",
        help="最多拉取 MA120 的股息+PE 幸存数（仅测试，日报不用）",
    )
    p_dj.add_argument(
        "--screen-only",
        dest="screen_only",
        action="store_true",
        help="只筛选、不写出技术面/财务个股包（仅测试，日报不用）",
    )
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = parse_args()
    setup_logging(args.verbose)
    cfg = load_config(args.config)

    if not cfg_get(cfg, "network.use_system_proxy", False):
        _disable_proxies()

    if args.command == "screen":
        df = run_screening(cfg)
        if df is None:
            return 1
        save_screening(df, cfg)
        return 0

    if args.command == "download":
        return cmd_download(cfg, args)

    if args.command == "policies":
        return cmd_policies(cfg, args)

    if args.command == "update-policies":
        return cmd_policies(cfg, args, update_mode=True)

    if args.command == "finance":
        return cmd_finance(cfg, args)

    if args.command == "run":
        df = run_screening(cfg)
        if df is None:
            return 1
        save_screening(df, cfg)
        if df.empty:
            log.info("无符合条件的股票，跳过下载")
            return 0
        stocks = [
            (str(c).zfill(6), str(n), str(i))
            for c, n, i in zip(df["代码"], df["名称"], df["所处行业"])
        ]
        return cmd_download(cfg, args, stocks=stocks)

    if args.command == "dianjin":
        return cmd_dianjin(cfg, args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
