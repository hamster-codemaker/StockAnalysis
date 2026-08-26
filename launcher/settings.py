"""用户开关：每次从磁盘读取，禁止把进程内缓存当真理。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from launcher.paths import settings_path

log = logging.getLogger("launcher")

DEFAULT_EARNINGS_MONTHS = [1, 2, 3, 4, 7, 8, 10]
SETTINGS_HEADER = """# 成长股工具 — 开机自启与每日推送开关（单一真相来源）
# 改本文件 或 在统一 GUI「定时任务」页勾选，效果相同。
# GUI 每次切换开关会立刻写回本文件；每次运行都重新读取本文件。
# report_dir 为空则默认「桌面/日报集」，其下再按日期建子目录；不要写到桌面根目录。
"""


@dataclass
class Settings:
    autostart: bool = False
    daily_update: bool = False
    daily_time: str = "16:00"
    report_dir: str = ""
    earnings_season_months: list[int] = field(default_factory=lambda: list(DEFAULT_EARNINGS_MONTHS))

    def is_earnings_season(self, month: int | None = None) -> bool:
        from datetime import datetime

        m = int(month if month is not None else datetime.now().month)
        return m in set(int(x) for x in self.earnings_season_months)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def normalize_daily_time(raw: Any, default: str = "16:00") -> str:
    text = str(raw or "").strip()
    if not text:
        return default
    parts = text.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        return default
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default
    return f"{hour:02d}:{minute:02d}"


def _as_report_dir(raw: Any) -> str:
    return str(raw or "").strip().strip('"').strip("'")


def resolve_report_root(settings: Settings | None = None) -> Path:
    """日报根目录：空则「桌面/日报集」，否则用配置中的绝对或相对路径。"""
    from launcher.paths import project_root, windows_desktop

    current = settings if settings is not None else load_settings()
    raw = _as_report_dir(current.report_dir)
    if not raw:
        return windows_desktop() / "日报集"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = project_root() / path
    return path


def dated_report_dir(day: str | None = None, settings: Settings | None = None) -> Path:
    """本次日报目录：<report_root>/<YYYYMMDD>/，不再写到桌面根目录。"""
    from datetime import datetime

    stamp = day or datetime.now().strftime("%Y%m%d")
    return resolve_report_root(settings) / stamp


def _months(raw: Any) -> list[int]:
    if not raw:
        return list(DEFAULT_EARNINGS_MONTHS)
    out: list[int] = []
    for item in raw:
        try:
            month = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= month <= 12 and month not in out:
            out.append(month)
    return out or list(DEFAULT_EARNINGS_MONTHS)


def settings_from_mapping(data: dict[str, Any] | None) -> Settings:
    data = data or {}
    return Settings(
        autostart=_as_bool(data.get("autostart"), False),
        daily_update=_as_bool(data.get("daily_update"), False),
        daily_time=normalize_daily_time(data.get("daily_time"), "16:00"),
        report_dir=_as_report_dir(data.get("report_dir")),
        earnings_season_months=_months(data.get("earnings_season_months")),
    )


def load_settings(path: Path | None = None) -> Settings:
    """始终从磁盘读取。文件不存在则写入默认值后再读。"""
    file_path = Path(path) if path else settings_path()
    if not file_path.is_file():
        save_settings(Settings(), file_path)
    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            log.warning("配置文件格式无效，使用默认值：%s", file_path)
            return Settings()
        return settings_from_mapping(raw)
    except OSError as exc:
        log.warning("读取配置失败，使用默认值：%s（%s）", file_path, exc)
        return Settings()


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    """立刻落盘。调用方在改 GUI 开关后必须走这里，不能只改内存。"""
    file_path = Path(path) if path else settings_path()
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "autostart": bool(settings.autostart),
        "daily_update": bool(settings.daily_update),
        "daily_time": normalize_daily_time(settings.daily_time),
        "report_dir": _as_report_dir(settings.report_dir),
        "earnings_season_months": [int(m) for m in settings.earnings_season_months],
    }
    body = SETTINGS_HEADER + yaml.safe_dump(
        payload, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    tmp = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(file_path)
    return file_path
