"""东方财富研报中心公开接口：个股/行业研报列表与PDF直链（无需API Key）。"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from ..downloader import HttpClient

log = logging.getLogger(__name__)

LIST_URL = "https://reportapi.eastmoney.com/report/list"
PDF_URL_TMPL = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"
REFERER = "https://data.eastmoney.com/report/"


class EastmoneyClient:
    def __init__(self, http: HttpClient):
        self.http = http

    def _query(
        self,
        q_type: str,
        code: str = "*",
        industry_code: str = "*",
        months: int = 6,
        max_pages: int = 2,
    ) -> list[dict]:
        """研报列表查询。q_type: 0=个股研报, 1=行业研报。"""
        end = date.today()
        begin = end - timedelta(days=months * 30)
        results: list[dict] = []
        page = 1
        while page <= max_pages:
            params = {
                "industryCode": industry_code,
                "pageSize": "100",
                "industry": "*",
                "rating": "*",
                "ratingChange": "*",
                "beginTime": begin.isoformat(),
                "endTime": end.isoformat(),
                "pageNo": str(page),
                "fields": "",
                "qType": q_type,
                "orgCode": "",
                "code": code,
                "rcode": "",
                "p": str(page),
                "pageNum": str(page),
                "pageNumber": str(page),
            }
            data = self.http.request_json(
                "GET", LIST_URL, params=params, headers={"Referer": REFERER}
            )
            if not data or not data.get("data"):
                break
            results.extend(self._normalize(item) for item in data["data"])
            if page >= int(data.get("TotalPage") or 1):
                break
            page += 1
        return results

    @staticmethod
    def _normalize(item: dict) -> dict:
        return {
            "title": str(item.get("title", "")).strip(),
            "org": item.get("orgSName") or item.get("orgName") or "",
            "date": str(item.get("publishDate", ""))[:10],
            "info_code": str(item.get("infoCode", "")),
            "rating": item.get("sRatingName") or item.get("emRatingName") or "",
            "eps_this": item.get("predictThisYearEps", ""),
            "eps_next": item.get("predictNextYearEps", ""),
            "pe_this": item.get("predictThisYearPe", ""),
            "pe_next": item.get("predictNextYearPe", ""),
            "industry_code": str(item.get("indvInduCode") or item.get("industryCode") or ""),
            "industry_name": item.get("indvInduName") or item.get("industryName") or "",
        }

    def stock_reports(
        self,
        code: str,
        months: int = 6,
        ratings: list[str] | None = None,
        max_count: int = 10,
    ) -> tuple[list[dict], str, str]:
        """个股研报（按评级子串过滤、按日期取最新N篇）。

        返回 (研报列表, 行业代码, 行业名称)，行业信息用于后续行业研报查询。
        """
        items = self._query("0", code=code, months=months)
        industry_code = next((i["industry_code"] for i in items if i["industry_code"]), "")
        industry_name = next((i["industry_name"] for i in items if i["industry_name"]), "")
        if ratings:
            selected = [
                i for i in items if any(r in (i["rating"] or "") for r in ratings)
            ]
        else:
            selected = list(items)
        selected.sort(key=lambda i: i["date"], reverse=True)
        return selected[:max_count], industry_code, industry_name

    def industry_reports(
        self, industry_code: str, months: int = 3, max_count: int = 5
    ) -> list[dict]:
        """指定行业的行业研报，按日期取最新N篇。"""
        if not industry_code:
            return []
        items = self._query("1", industry_code=industry_code, months=months, max_pages=1)
        items.sort(key=lambda i: i["date"], reverse=True)
        return items[:max_count]

    @staticmethod
    def pdf_url(info_code: str) -> str:
        return PDF_URL_TMPL.format(info_code=info_code)
