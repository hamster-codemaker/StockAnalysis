"""面向公开 HTTP 接口的普通 GET：直连、带 UA，不伪造 TLS 指纹。"""

from __future__ import annotations

from typing import Any

import requests

from dianjin.em_cluster import CHROME_UA, disable_proxy_env


def public_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 12.0,
) -> requests.Response:
    """普通 HTTPS GET。不使用 curl_cffi / impersonate。"""
    disable_proxy_env()
    session = requests.Session()
    session.trust_env = False
    merged = {"User-Agent": CHROME_UA}
    if headers:
        merged.update(headers)
    return session.get(url, params=params or {}, headers=merged, timeout=timeout)
