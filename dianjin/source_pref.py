"""行情源优先级：腾讯优先、其它备用、东财最后；失败且备用成功则对调并持久化。

写入 userdata/source_preference.yaml（不是密钥，不打进安装包）。
新安装无此文件时使用代码内默认顺序。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("dianjin")

CHANNELS = ("hist", "kline", "spot")
DEFAULT_ORDER: dict[str, list[str]] = {
    "hist": ["tencent"],
    "kline": ["tencent"],
    "spot": ["tencent", "datacenter"],
}
# 已停用：东财 push2/push2his clist；新浪行情/日线/机构持股。
RETIRED_SOURCES: dict[str, set[str]] = {
    "hist": {"eastmoney", "sina"},
    "kline": {"eastmoney", "sina"},
    "spot": {"clist"},
}

PREF_HEADER = """# 自动维护的行情源优先级。不是密钥，不要提交到 git。
# 新安装默认：腾讯优先；全 A 排行备用东财数据中心。不再使用新浪、东财 push2。
# 某主源对一次请求全部重试失败、且备用源成功时，会对调并写回，供下次启动使用。
"""

_PATH_OVERRIDE: Path | None = None


def set_preference_path(path: Path | None) -> None:
    """测试用：把偏好文件指到临时目录。"""
    global _PATH_OVERRIDE
    _PATH_OVERRIDE = Path(path) if path is not None else None


def preference_path() -> Path:
    if _PATH_OVERRIDE is not None:
        return _PATH_OVERRIDE
    try:
        from launcher.paths import userdata_dir

        return userdata_dir() / "source_preference.yaml"
    except Exception:
        return Path("userdata") / "source_preference.yaml"


def _normalize_order(channel: str, raw: Any) -> list[str]:
    default = list(DEFAULT_ORDER[channel])
    retired = RETIRED_SOURCES.get(channel, set())
    if not isinstance(raw, (list, tuple)):
        return default
    seen: list[str] = []
    for item in raw:
        name = str(item).strip().lower()
        if name in retired:
            continue
        if name in default and name not in seen:
            seen.append(name)
    for name in default:
        if name not in seen:
            seen.append(name)
    return seen


def load_preference() -> dict[str, Any]:
    path = preference_path()
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            import yaml

            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                data = loaded
        except Exception as exc:
            log.debug("读取源偏好失败，用默认：%s", exc)
    out: dict[str, Any] = {
        channel: _normalize_order(channel, (data.get(channel) or {}).get("order") if isinstance(data.get(channel), dict) else data.get(channel))
        for channel in CHANNELS
    }
    host = str(data.get("clist_preferred_host") or "").strip()
    if host:
        out["clist_preferred_host"] = host
    return out


def save_preference(pref: dict[str, Any]) -> None:
    path = preference_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        channel: {"order": list(pref.get(channel) or DEFAULT_ORDER[channel])}
        for channel in CHANNELS
    }
    host = str(pref.get("clist_preferred_host") or "").strip()
    if host:
        payload["clist_preferred_host"] = host
    import yaml

    text = PREF_HEADER + yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8")


def source_order(channel: str) -> list[str]:
    if channel not in DEFAULT_ORDER:
        raise KeyError(channel)
    return list(load_preference()[channel])


def clist_preferred_host() -> str | None:
    host = str(load_preference().get("clist_preferred_host") or "").strip()
    return host or None


def save_clist_preferred_host(host: str) -> None:
    pref = load_preference()
    pref["clist_preferred_host"] = host
    save_preference(pref)


def promote_backup(channel: str, failed: str, succeeded: str) -> bool:
    """主源整轮失败且备用成功：对调二者并写盘。两边都失败不要调用。"""
    if channel not in DEFAULT_ORDER:
        return False
    failed_n = str(failed).strip().lower()
    ok_n = str(succeeded).strip().lower()
    if not failed_n or not ok_n or failed_n == ok_n:
        return False
    pref = load_preference()
    order = list(pref[channel])
    if failed_n not in order or ok_n not in order:
        return False
    i, j = order.index(failed_n), order.index(ok_n)
    if i > j:
        return False
    order[i], order[j] = order[j], order[i]
    pref[channel] = order
    save_preference(pref)
    log.info("源优先级已对调（%s）：%s ↔ %s → %s", channel, failed_n, ok_n, " > ".join(order))
    return True


class SourceBreaker:
    """本进程内跳过已确认整轮失败的源，只公告一次。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self._dead: set[str] = set()
        self._announced: set[str] = set()

    def reset(self) -> None:
        self._dead.clear()
        self._announced.clear()

    def is_dead(self, source: str) -> bool:
        return source in self._dead

    def mark_dead(self, source: str, logger: logging.Logger | None = None, message: str = "") -> None:
        self._dead.add(source)
        if source in self._announced:
            return
        self._announced.add(source)
        (logger or log).info("%s", message or f"{self.name} 源 {source} 本轮不可达，改用后续备用源")
