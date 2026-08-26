"""通用HTTP客户端：全局限速、失败重试、PDF魔数校验、manifest增量下载。"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

import requests

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')
_HTML_TAG = re.compile(r"<[^>]+>")


def sanitize_filename(name: str, max_len: int = 100) -> str:
    """去掉HTML标签与文件系统非法字符，限制长度。"""
    name = _HTML_TAG.sub("", str(name))
    name = _ILLEGAL_CHARS.sub("", name).replace("\u3000", " ").strip().strip(".")
    return name[:max_len].strip() or "unnamed"


class HttpClient:
    """带全局限速与指数退避重试的HTTP客户端。

    下载成功的文件记录在 manifest（JSON）中，重复运行时自动跳过，实现增量下载。
    """

    def __init__(
        self,
        rate_limit_seconds: float = 1.5,
        max_retries: int = 3,
        timeout_seconds: int = 30,
        manifest_path: str | Path | None = None,
        use_system_proxy: bool = False,
    ):
        self.session = requests.Session()
        self.session.trust_env = bool(use_system_proxy)
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.rate_limit = float(rate_limit_seconds)
        self.max_retries = int(max_retries)
        self.timeout = int(timeout_seconds)
        self._last_request = 0.0
        self.manifest_path = Path(manifest_path) if manifest_path else None
        self.manifest: dict = {}
        if self.manifest_path and self.manifest_path.exists():
            try:
                self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("manifest 文件损坏，将重建：%s", self.manifest_path)

    def _throttle(self) -> None:
        wait = self.rate_limit - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def request(self, method: str, url: str, **kwargs) -> requests.Response | None:
        kwargs.setdefault("timeout", self.timeout)
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.request(method, url, **kwargs)
                if resp.status_code == 200:
                    return resp
                log.warning("HTTP %s -> 状态码 %s（第%d次）：%s",
                            method, resp.status_code, attempt, url)
                if resp.status_code == 404:
                    return None  # 资源不存在，重试无意义
            except requests.RequestException as exc:
                log.warning("请求异常（第%d次）%s：%s", attempt, url, exc)
            if attempt < self.max_retries:
                time.sleep(min(2 ** attempt * 2, 30))
        return None

    def request_json(self, method: str, url: str, **kwargs):
        resp = self.request(method, url, **kwargs)
        if resp is None:
            return None
        try:
            return resp.json()
        except ValueError:
            log.warning("响应不是有效JSON：%s", url)
            return None

    def download_pdf(
        self,
        url: str,
        dest: Path,
        referer: str | None = None,
        impersonate: bool = False,
    ) -> str:
        """下载PDF到 dest，返回 'ok' / 'skip' / 'fail'。

        manifest 已记录且文件仍在磁盘上，或目标文件已是有效PDF时跳过。
        impersonate 参数保留兼容，忽略：只走普通 HTTPS，不伪造 TLS。
        """
        del impersonate
        entry = self.manifest.get(url)
        if entry and Path(entry.get("path", "")).exists():
            return "skip"
        if dest.exists() and self._is_pdf(dest):
            self._record(url, dest)
            return "skip"

        data = self._fetch_plain(url, referer)
        if data is None or not data.startswith(b"%PDF"):
            log.warning("未能获取有效PDF：%s", url)
            return "fail"

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            tmp.write_bytes(data)
            tmp.replace(dest)
        except OSError as exc:
            log.warning("写入文件失败 %s：%s", dest, exc)
            tmp.unlink(missing_ok=True)
            return "fail"
        self._record(url, dest)
        return "ok"

    def _fetch_plain(self, url: str, referer: str | None) -> bytes | None:
        headers = {"Referer": referer} if referer else {}
        resp = self.request("GET", url, headers=headers)
        return resp.content if resp is not None else None

    @staticmethod
    def _is_pdf(path: Path) -> bool:
        try:
            with open(path, "rb") as f:
                return f.read(5).startswith(b"%PDF")
        except OSError:
            return False

    def _record(self, url: str, dest: Path) -> None:
        self.manifest[url] = {
            "path": str(dest),
            "size": dest.stat().st_size if dest.exists() else 0,
            "time": datetime.now().isoformat(timespec="seconds"),
        }
        if not self.manifest_path:
            return
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tmp.replace(self.manifest_path)
