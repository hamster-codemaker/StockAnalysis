"""加载本目录 config.yaml，与主项目配置完全独立。"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent

DEFAULTS: dict[str, Any] = {
    "lookback_trading_days": 120,
    "warmup_extra_days": 130,
    "ma": {"short": 8, "long": 24, "trend": 120},
    "macd": {
        "fast": 12,
        "slow": 26,
        "signal": 9,
        "divergence_window": 60,
        "divergence_min_gap": 5,
    },
    "bollinger": {"period": 20, "std_mult": 2.0},
    "rsi": {"period": 6, "period2": 12, "period3": 24, "overbought": 80, "oversold": 20},
    "network": {
        "rate_limit_seconds": 1.5,
        "max_retries": 3,
        "timeout_seconds": 30,
        "use_system_proxy": False,
    },
    "paths": {
        "watchlist": "watchlist.txt",
        "output_dir": "output",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path | None = None) -> dict:
    config_path = Path(path) if path else PACKAGE_DIR / "config.yaml"
    if not config_path.is_absolute():
        candidate = Path(config_path)
        config_path = candidate if candidate.exists() else PACKAGE_DIR / candidate
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
        return _deep_merge(DEFAULTS, user_cfg)
    return copy.deepcopy(DEFAULTS)


def cfg_get(cfg: dict, dotted: str, default: Any = None) -> Any:
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def resolve_under_package(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PACKAGE_DIR / p
