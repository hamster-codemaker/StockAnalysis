"""国务院政策文件库（www.gov.cn/zhengce/zhengceku）公开搜索接口。

聚合国务院文件与各部委文件。检索策略分两层：
1. 标题检索（searchfield=title）——相关度高，优先收录；
2. 全文检索（searchfield=title:content）——数量补充，凑满目标篇数。
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..downloader import HttpClient

log = logging.getLogger(__name__)

SEARCH_URL = "https://sousuo.www.gov.cn/search-gov/data"
REFERER = "https://www.gov.cn/zhengce/zhengceku/"

# 搜索结果类别 -> 来源名称（公报类多为重复刊发，不收录）
_CATEGORIES = (("gongwen", "国务院文件"), ("bumenfile", "部门文件"))
_TAG = re.compile(r"<[^>]+>")
_ATTACHMENT = re.compile(r"\.(pdf|doc|docx|xls|xlsx)($|\?)", re.I)
_ROMAN_SUFFIX = re.compile(r"[ⅠⅡⅢⅣ]+$")


def clean_industry_keyword(industry: str) -> str:
    """行业名转搜索关键词：去掉罗马数字后缀等（如 航空装备Ⅱ -> 航空装备）。"""
    return _ROMAN_SUFFIX.sub("", str(industry).strip())


def _strip_tags(text: str) -> str:
    return _TAG.sub("", str(text)).replace("\u3000", " ").strip()


class GovPolicyClient:
    def __init__(self, http: HttpClient):
        self.http = http

    def search(
        self, keyword: str, max_count: int = 30, fulltext_supplement: bool = True
    ) -> list[dict]:
        """检索政策文件，返回按收录顺序去重后的条目列表。"""
        found: dict[str, dict] = {}
        self._collect(keyword, "title", max_count, found)
        title_hits = len(found)
        if fulltext_supplement and title_hits < max_count:
            self._collect(keyword, "title:content", max_count, found)
        items = list(found.values())[:max_count]
        log.info(
            "  政策检索「%s」：标题命中 %d，补充后共 %d 篇", keyword, title_hits, len(items)
        )
        return items

    def _collect(
        self, keyword: str, searchfield: str, max_count: int, out: dict[str, dict]
    ) -> None:
        page = 1
        while len(out) < max_count and page <= 10:
            params = {
                "t": "zhengcelibrary_gw_bm_gb_gwyzcwjk",
                "q": keyword,
                "timetype": "timeqb",
                "mintime": "",
                "maxtime": "",
                "sort": "pubtime",
                "sortType": "1",
                "searchfield": searchfield,
                "pcodeJiguan": "",
                "childtype": "",
                "subchildtype": "",
                "tsbq": "",
                "pubtimeyear": "",
                "puborg": "",
                "pcodeYear": "",
                "pcodeNum": "",
                "filetype": "",
                "p": str(page),
                "n": "20",
                "inpro": "",
                "bmfl": "",
                "dup": "",
                "orpro": "",
                "type": "gwyzcwjk",
            }
            data = self.http.request_json(
                "GET", SEARCH_URL, params=params, headers={"Referer": REFERER}
            )
            if not data or data.get("code") != 200:
                break  # code=1001 表示无结果
            cat_map = ((data.get("searchVO") or {}).get("catMap")) or {}
            page_hits = 0
            for cat_key, source in _CATEGORIES:
                for item in ((cat_map.get(cat_key) or {}).get("listVO")) or []:
                    page_hits += 1
                    normalized = self._normalize(item, source)
                    key = normalized["url"] or str(item.get("id"))
                    if key and key not in out:
                        out[key] = normalized
            if page_hits == 0:
                break
            page += 1

    @staticmethod
    def _normalize(item: dict, source: str) -> dict:
        date = str(item.get("pubtimeStr", "")).replace(".", "-")
        return {
            "title": _strip_tags(item.get("title", "")),
            "url": str(item.get("url", "")),
            "date": date,
            "org": _strip_tags(item.get("puborg", "")),
            "pcode": _strip_tags(item.get("pcode", "")),
            "theme": _strip_tags(item.get("childtype", "")),
            "summary": _strip_tags(item.get("summary", "")),
            "source": source,
        }

    def fetch_detail(self, url: str) -> tuple[str, list[str]]:
        """抓取政策详情页，返回（正文纯文本, 附件绝对链接列表）。

        gov.cn 详情页HTML不规范，lxml 可能静默丢失正文节点（不抛异常），
        因此逐个解析器尝试，直到定位到正文容器为止。
        """
        resp = self.http.request("GET", url, headers={"Referer": REFERER})
        if resp is None:
            return "", []
        resp.encoding = "utf-8"
        node = soup = None
        for parser in ("lxml", "html.parser"):
            try:
                soup = BeautifulSoup(resp.text, parser)
            except Exception:
                continue
            node = (
                soup.find(id="UCAP-CONTENT")
                or soup.find(class_="pages_content")
                or soup.find(class_="article")
            )
            if node is not None:
                break
        if node is None:
            node = soup.body if soup is not None else None
        if node is None:
            return "", []
        text = node.get_text("\n", strip=True)
        attachments = []
        for a in node.find_all("a", href=True):
            href = a["href"].strip()
            if _ATTACHMENT.search(href):
                attachments.append(urljoin(url, href))
        return text, attachments
