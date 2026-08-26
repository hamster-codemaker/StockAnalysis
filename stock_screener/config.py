"""配置加载：config.yaml 与内置默认值深度合并。"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "screening": {
        "include_bse": False,
        "exclude_st": True,
        "market_cap_max_yi": 200.0,
        "growth": {
            "revenue_yoy_min": 40.0,
            "profit_yoy_min": 40.0,
            "consecutive_periods": 1,
            "periods_lookback": 6,
        },
        "institution": {
            "min_institutions": 1,
            "max_float_ratio_pct": 20.0,
            "quarters_lookback": 2,
        },
    },
    "downloads": {
        "output_dir": "data/docs",
        "prospectus": {"enabled": True},
        "financial_reports": {
            "enabled": True,
            "types": ["年报", "半年报", "一季报", "三季报"],
            "since": "1990-01-01",
        },
        "research_reports": {
            "enabled": True,
            "stock": {"months": 6, "max_count": 10, "ratings": ["买入", "增持"]},
            "industry": {"enabled": True, "months": 3, "max_count": 5},
        },
        "policies": {
            "enabled": True,
            "per_industry_max": 30,
            "fulltext_supplement": True,
            "output_dir": "data/policies",
        },
        "financial_analysis": {
            "enabled": True,
        },
    },
    "dianjin": {
        "dividend_min": 3.0,
        "extra_dividend_min": 4.0,
        "pe_max": 20.0,
        "ma120_ratio": 0.88,
        "extra_ma120_ratio": 0.82,
        "ma_period": 120,
        "exclude_st": True,
        "include_bse": False,
        "recent_signal_days": 5,
        "kline_rate_limit": 0.3,
        "snapshot_page_size": 100,
        "snapshot_sleep": 0.15,
    },
    "network": {
        "rate_limit_seconds": 1.5,
        "max_retries": 3,
        "timeout_seconds": 30,
        "use_system_proxy": False,
    },
    "paths": {
        "screening_dir": "data/screening",
        "dianjin_dir": "data/dianjin",
        "cache_dir": "data/cache",
        "manifest": "data/manifest.json",
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
    """读取 YAML 配置并与默认值合并；文件不存在时直接返回默认配置。"""
    config_path = Path(path) if path else Path("config.yaml")
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
        return _deep_merge(DEFAULTS, user_cfg)
    return copy.deepcopy(DEFAULTS)


def cfg_get(cfg: dict, dotted: str, default: Any = None) -> Any:
    """按点分路径取配置值，如 cfg_get(cfg, "screening.growth.revenue_yoy_min")。"""
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node
