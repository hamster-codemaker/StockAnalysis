"""巨潮资讯网（证监会指定信息披露平台）公开公告接口。

功能：股票代码到 orgId 的映射、招股说明书检索、全量定期财报检索、PDF 直链解析。
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime
from pathlib import Path

from ..downloader import HttpClient

log = logging.getLogger(__name__)

BASE_URL = "https://www.cninfo.com.cn"
QUERY_URL = f"{BASE_URL}/new/hisAnnouncement/query"
STOCK_JSON_URL = f"{BASE_URL}/new/data/szse_stock.json"
STATIC_URL = "https://static.cninfo.com.cn/"
REFERER = f"{BASE_URL}/new/commonUrl/pageOfSearch?url=disclosure/list/search"

# 定期报告的官方公告分类（沪深通用）
REPORT_CATEGORIES = {
    "年报": "category_ndbg_szsh",
    "半年报": "category_bndbg_szsh",
    "一季报": "category_yjdbg_szsh",
    "三季报": "category_sjdbg_szsh",
}
IPO_CATEGORY = "category_sf_szsh"  # 首发（含招股说明书）

_REPORT_EXCLUDES = ("摘要", "已取消", "取消公告", "英文")
_PROSPECTUS_EXCLUDES = ("摘要", "意向书", "英文", "提示")
_TAG = re.compile(r"<[^>]+>")
_LOCAL_STOCKS: dict[str, dict] | None = None


def _clean_title(title: str) -> str:
    return _TAG.sub("", str(title)).replace("\u3000", " ").strip()


def _index_stock_list(data: object) -> dict[str, dict]:
    stocks = (data or {}).get("stockList", []) if isinstance(data, dict) else []
    return {str(s["code"]).zfill(6): s for s in stocks if isinstance(s, dict) and s.get("code")}


def _share_local_stocks(stocks: dict[str, dict]) -> None:
    global _LOCAL_STOCKS
    _LOCAL_STOCKS = stocks


def reset_local_stock_map() -> None:
    """测试用：清空进程内巨潮映射，避免跨用例串数据。"""
    global _LOCAL_STOCKS
    _LOCAL_STOCKS = None


def _zwjc(info: dict | None) -> str | None:
    if not info:
        return None
    name = str(info.get("zwjc") or "").strip()
    return name or None


def peek_stock_name(code: str, cache_files: list[Path] | None = None) -> str | None:
    """从已加载映射或现有 szse_stock.json 取 zwjc。不下载全表。"""
    code = str(code).zfill(6)
    if _LOCAL_STOCKS is not None:
        return _zwjc(_LOCAL_STOCKS.get(code) or _LOCAL_STOCKS.get(str(code)))
    files = list(cache_files) if cache_files is not None else []
    if cache_files is None:
        try:
            from launcher.paths import project_root

            files.append(project_root() / "data" / "cache" / "szse_stock.json")
        except Exception:
            pass
        files.append(Path("data/cache/szse_stock.json"))
    seen: set[str] = set()
    for path in files:
        key = str(path)
        try:
            if path.is_file():
                key = str(path.resolve())
        except OSError:
            pass
        if key in seen:
            continue
        seen.add(key)
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stocks = _index_stock_list(data)
        if not stocks:
            continue
        _share_local_stocks(stocks)
        return _zwjc(stocks.get(code))
    return None


class CninfoClient:
    def __init__(self, http: HttpClient, cache_dir: str | Path = "data/cache"):
        self.http = http
        self.cache_dir = Path(cache_dir)
        self._stocks: dict[str, dict] | None = None

    # ---------- 代码 -> orgId 映射 ----------

    def stock_map(self) -> dict[str, dict]:
        """全市场 代码->{orgId, zwjc} 映射，本地缓存7天。"""
        if self._stocks is not None:
            return self._stocks
        cache_file = self.cache_dir / "szse_stock.json"
        data = None
        if cache_file.exists() and time.time() - cache_file.stat().st_mtime < 7 * 86400:
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
        if not data:
            data = self.http.request_json("GET", STOCK_JSON_URL, headers={"Referer": REFERER})
            if data:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        stocks = (data or {}).get("stockList", [])
        self._stocks = {s["code"]: s for s in stocks if s.get("code")}
        _share_local_stocks({str(k).zfill(6): v for k, v in self._stocks.items()})
        log.info("巨潮股票映射表加载完成：%d 条", len(self._stocks))
        return self._stocks

    def org_id(self, code: str) -> str | None:
        info = self.stock_map().get(code)
        return info.get("orgId") if info else None

    def stock_name(self, code: str) -> str | None:
        info = self.stock_map().get(code)
        return info.get("zwjc") if info else None

    def stock_name_local(self, code: str) -> str | None:
        """内存或已有缓存文件中的 zwjc，不触发全量下载。"""
        code6 = str(code).zfill(6)
        if self._stocks is not None:
            info = self._stocks.get(code6) or self._stocks.get(code)
            return _zwjc(info)
        return peek_stock_name(code6, cache_files=[self.cache_dir / "szse_stock.json"])

    @staticmethod
    def _column(code: str) -> str:
        if code.startswith("6"):
            return "sse"
        if code.startswith(("0", "3")):
            return "szse"
        return "bj"

    # ---------- 公告查询 ----------

    def query_announcements(
        self,
        code: str,
        category: str = "",
        searchkey: str = "",
        since: str = "1990-01-01",
    ) -> list[dict]:
        """分页查询某只股票的公告，返回 [{id, title, date, url}]（仅PDF附件）。"""
        org_id = self.org_id(code)
        if not org_id:
            log.warning("未在巨潮映射表中找到 %s 的 orgId，跳过公告查询", code)
            return []
        results: list[dict] = []
        se_date = f"{since}~{date.today().isoformat()}"
        page = 1
        while page <= 100:
            form = {
                "pageNum": str(page),
                "pageSize": "30",
                "column": self._column(code),
                "tabName": "fulltext",
                "plate": "",
                "stock": f"{code},{org_id}",
                "searchkey": searchkey,
                "secid": "",
                "category": category,
                "trade": "",
                "seDate": se_date,
                "sortName": "",
                "sortType": "",
                "isHLtitle": "false",
            }
            data = self.http.request_json(
                "POST",
                QUERY_URL,
                data=form,
                headers={"Referer": REFERER, "X-Requested-With": "XMLHttpRequest"},
            )
            if data is None:
                log.warning("巨潮公告查询失败：%s 第%d页", code, page)
                break
            for item in data.get("announcements") or []:
                if str(item.get("adjunctType", "")).upper() != "PDF":
                    continue
                ts = item.get("announcementTime") or 0
                results.append({
                    "id": str(item.get("announcementId", "")),
                    "title": _clean_title(item.get("announcementTitle", "")),
                    "date": datetime.fromtimestamp(ts / 1000).date().isoformat() if ts else "",
                    "url": STATIC_URL + str(item.get("adjunctUrl", "")),
                })
            if not data.get("hasMore"):
                break
            page += 1
        return results

    def periodic_reports(
        self, code: str, types: list[str], since: str = "1990-01-01"
    ) -> list[dict]:
        """上市以来的全部定期报告（按 types 指定类型），排除摘要/英文版/已取消。"""
        categories = ";".join(
            REPORT_CATEGORIES[t] for t in types if t in REPORT_CATEGORIES
        )
        if not categories:
            return []
        items = self.query_announcements(code, category=categories, since=since)
        reports = [
            i for i in items if not any(k in i["title"] for k in _REPORT_EXCLUDES)
        ]
        reports.sort(key=lambda i: i["date"], reverse=True)
        return reports

    def prospectus(self, code: str) -> list[dict]:
        """最新正式版招股说明书（排除摘要/意向书）；找不到时退回关键词搜索。"""

        def _pick(items: list[dict]) -> list[dict]:
            return [
                i for i in items
                if "招股说明书" in i["title"]
                and not any(k in i["title"] for k in _PROSPECTUS_EXCLUDES)
            ]

        found = _pick(self.query_announcements(code, category=IPO_CATEGORY))
        if not found:
            found = _pick(self.query_announcements(code, searchkey="招股说明书"))
        found.sort(key=lambda i: i["date"], reverse=True)
        return found[:1]
