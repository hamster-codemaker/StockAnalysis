"""绕过系统代理、全局限速与失败重试（逻辑自包含，不引用 stock_screener）。"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import TypeVar

import requests

log = logging.getLogger("tech_analysis")

T = TypeVar("T")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_PROXIES_DISABLED = False
_BROWSER_TLS_ENABLED = False


def disable_proxies() -> None:
    """绕过系统/环境代理直连（行情源为国内公开站点）。

    Windows 注册表代理（如 127.0.0.1:7897）仅靠 NO_PROXY 无法完全绕过，
    因此同时将后续创建的 requests.Session.trust_env 置为 False，
    使 akshare 内部请求同样直连。
    """
    global _PROXIES_DISABLED
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    if _PROXIES_DISABLED:
        return
    orig_init = requests.Session.__init__

    def init_without_proxy(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        self.trust_env = False
        self.headers.setdefault("User-Agent", USER_AGENT)

    requests.Session.__init__ = init_without_proxy
    _PROXIES_DISABLED = True


def enable_browser_tls() -> None:
    """兼容旧入口。生产路径已停用东财 push2，不再伪造浏览器 TLS。"""
    global _BROWSER_TLS_ENABLED
    if _BROWSER_TLS_ENABLED:
        return
    _BROWSER_TLS_ENABLED = True
    log.info("行情请求使用普通 HTTPS，不模拟浏览器 TLS")


class RateLimiter:
    """任意两次请求之间的最小间隔。"""

    def __init__(self, min_interval: float = 1.5):
        self.min_interval = float(min_interval)
        self._last = 0.0

    def wait(self) -> None:
        gap = self.min_interval - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()


def retry_call(
    func: Callable[..., T],
    *args,
    max_retries: int = 3,
    limiter: RateLimiter | None = None,
    what: str = "请求",
    **kwargs,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        if limiter is not None:
            limiter.wait()
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            log.warning("%s失败（第%d/%d次）：%s", what, attempt, max_retries, exc)
            if attempt < max_retries:
                time.sleep(min(2 ** attempt * 2, 30))
    assert last_exc is not None
    raise last_exc


def http_get_text(
    url: str,
    *,
    timeout: float = 8.0,
    headers: dict[str, str] | None = None,
    encodings: tuple[str, ...] = ("utf-8", "gb18030"),
) -> str:
    """普通 HTTPS GET，直连、不重试、不伪造 TLS。"""
    merged = {"User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)
    disable_proxies()
    session = requests.Session()
    session.trust_env = False
    resp = session.get(url, timeout=timeout, headers=merged)
    resp.raise_for_status()
    raw = resp.content or b""
    if not raw:
        return ""
    for enc in encodings:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
