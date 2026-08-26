"""东财 push2 / push2his 集群熔断（生产路径已停用这些主机）。

保留熔断器供单测与遗留函数使用。对外请求一律走普通 HTTPS，不伪造 TLS。
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("dianjin")

HARD_FAIL_TOKENS = (
    "(56)",
    "(28)",
    "(7)",
    "Connection closed",
    "Connection aborted",
    "RemoteDisconnected",
    "Failed to perform",
    "timed out",
    "Timeout",
    "Connection reset",
    "reset by peer",
)

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def disable_proxy_env() -> None:
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"


def is_hard_fail(exc: BaseException) -> bool:
    text = str(exc)
    name = type(exc).__name__
    blob = f"{name} {text}"
    return any(token in blob for token in HARD_FAIL_TOKENS)


def impersonated_get(url: str, params: dict[str, str], timeout: float, headers: dict[str, str]):
    """兼容旧测试名。改为普通 HTTPS GET，不再伪造浏览器 TLS。"""
    from dianjin.http_public import public_get

    return public_get(url, params=params, headers=headers, timeout=timeout)


class ClusterBreaker:
    """进程内东财主机族熔断：粘滞可用主机，硬失败主机不再试，连续失败后整族跳过。"""

    def __init__(
        self,
        name: str,
        hosts: tuple[str, ...],
        *,
        trip_after: int = 2,
        hard_fail_host_cap: int = 2,
        announce_message: str = "",
    ) -> None:
        self.name = name
        self.hosts = tuple(hosts)
        self.trip_after = max(1, int(trip_after))
        self.hard_fail_host_cap = max(1, int(hard_fail_host_cap))
        self.announce_message = announce_message
        self.last_good: str | None = None
        self._dead: set[str] = set()
        self._consecutive_failures = 0
        self._open = False
        self._announced = False

    def reset(self) -> None:
        self.last_good = None
        self._dead.clear()
        self._consecutive_failures = 0
        self._open = False
        self._announced = False

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def announced(self) -> bool:
        return self._announced

    def host_order(self) -> list[str]:
        if self._open:
            return []
        hosts = [h for h in self.hosts if h not in self._dead]
        if self.last_good in hosts:
            hosts.remove(self.last_good)
            hosts.insert(0, self.last_good)
        return hosts

    def should_stop(self, attempted: int, hard_seen: bool) -> bool:
        if self._open:
            return True
        return bool(hard_seen and attempted >= self.hard_fail_host_cap)

    def record_host_success(self, host: str) -> None:
        self.last_good = host
        self._dead.discard(host)
        self._consecutive_failures = 0

    def record_host_failure(self, host: str, exc: BaseException) -> bool:
        """记下单主机失败。硬失败则拉黑该主机。返回是否硬失败。"""
        hard = is_hard_fail(exc)
        if hard:
            self._dead.add(host)
        return hard

    def trip(self) -> bool:
        """打开熔断。若本次刚打开则返回 True。"""
        if self._open:
            return False
        self._open = True
        return True

    def record_attempt_failure(self, *, all_hard: bool = False) -> bool:
        """一轮（一只股票 / 一页）允许的主机都失败。全硬失败则立即熔断。"""
        self._consecutive_failures += 1
        if all_hard or self._consecutive_failures >= self.trip_after:
            return self.trip()
        return False

    def announce(self, logger: logging.Logger | None = None, message: str | None = None) -> None:
        if self._announced:
            return
        self._announced = True
        (logger or log).info("%s", message or self.announce_message)


def try_hosts(
    breaker: ClusterBreaker,
    attempt,
    *,
    debug_log=None,
) -> tuple[Any, str | None, Exception | None, int]:
    """依次尝试主机，硬失败时最多试 hard_fail_host_cap 个。

    attempt(host) 成功须返回真值；网络异常抛出。空结果视为该主机无数据（继续）。
    返回 (result, host, last_exc, hosts_tried)。
    """
    last_exc: Exception | None = None
    attempted = 0
    hard_seen = False
    all_hard = True
    saw_any = False
    for host in breaker.host_order():
        if breaker.should_stop(attempted, hard_seen):
            break
        attempted += 1
        saw_any = True
        try:
            result = attempt(host)
            if result:
                breaker.record_host_success(host)
                return result, host, None, attempted
            last_exc = RuntimeError(f"{host} 无数据")
            all_hard = False
        except Exception as exc:
            last_exc = exc
            hard = breaker.record_host_failure(host, exc)
            if not hard:
                all_hard = False
            else:
                hard_seen = True
            if debug_log is not None and not breaker.is_open:
                debug_log(host, exc)
    if saw_any:
        just_tripped = breaker.record_attempt_failure(all_hard=all_hard and attempted > 0)
        if just_tripped:
            breaker.announce()
    return None, None, last_exc, attempted
