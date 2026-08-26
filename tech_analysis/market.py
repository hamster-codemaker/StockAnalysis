"""A 股前复权日线与证券简称。简称：本地优先，腾讯。"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .config import cfg_get
from .network import RateLimiter, retry_call

log = logging.getLogger("tech_analysis")

_COL_MAP = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
}


def _import_akshare():
    import akshare as ak

    return ak


def _calendar_span(cfg: dict) -> tuple[str, str]:
    lookback = int(cfg_get(cfg, "lookback_trading_days", 120))
    extra = int(cfg_get(cfg, "warmup_extra_days", 130))
    # 交易日约 244/年，预留足够日历跨度
    cal_days = int((lookback + extra) * 2.0) + 30
    end = date.today()
    start = end - timedelta(days=cal_days)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _normalize_hist(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "date" not in out.columns and out.index.name in {"date", "日期"}:
        out = out.reset_index()
    out = out.rename(columns={k: v for k, v in _COL_MAP.items() if k in out.columns})
    lower = {str(c).lower(): c for c in out.columns}
    for std in ("date", "open", "close", "high", "low", "volume"):
        if std not in out.columns and std in lower:
            out = out.rename(columns={lower[std]: std})
    if "date" not in out.columns or "close" not in out.columns:
        raise ValueError(f"日线缺少必要列：{list(out.columns)}")
    out["date"] = pd.to_datetime(out["date"])
    for col in ("open", "close", "high", "low", "volume"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["date", "close"]).sort_values("date")
    out = out.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    return out


def _market_symbol(code: str) -> str:
    if code.startswith(("6", "9")):
        return f"sh{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sz{code}"


def fetch_daily(code: str, cfg: dict, limiter: RateLimiter) -> pd.DataFrame:
    """拉取前复权日线：腾讯。"""
    from dianjin.kline import (
        KLINE_SOURCE_BREAKER,
        bars_to_frame,
        fetch_eastmoney_bars,
        fetch_tencent_bars,
    )
    from dianjin.source_pref import promote_backup, source_order

    start, end = _calendar_span(cfg)
    timeout = float(cfg_get(cfg, "network.timeout_seconds", 30))
    max_retries = int(cfg_get(cfg, "network.max_retries", 3))
    lookback = int(cfg_get(cfg, "lookback_trading_days", 120))
    extra = int(cfg_get(cfg, "warmup_extra_days", 130))
    bar_limit = max(lookback + extra, 250)
    labels = {"tencent": "腾讯日线", "eastmoney": "东财日线"}
    loaders = {
        "tencent": lambda: bars_to_frame(fetch_tencent_bars(code, limit=bar_limit, timeout=timeout)),
        "eastmoney": lambda: bars_to_frame(fetch_eastmoney_bars(code, limit=bar_limit, timeout=timeout)),
    }
    errors: list[str] = []
    failed: str | None = None
    log.info("拉取 %s 日线（%s～%s，前复权，腾讯）", code, start, end)
    for src in source_order("kline"):
        if KLINE_SOURCE_BREAKER.is_dead(src):
            continue
        loader = loaders.get(src)
        if loader is None:
            continue
        label = labels.get(src, src)
        try:
            df = retry_call(
                loader,
                max_retries=max_retries if src != "eastmoney" else 1,
                limiter=limiter,
                what=f"{code} {label}",
            )
            if df is None or df.empty:
                raise RuntimeError(f"{label}返回空表")
            if failed:
                promote_backup("kline", failed, src)
            log.info("%s 已用%s，共 %d 根K线", code, label, len(df))
            return df
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            if failed is None:
                failed = src
            KLINE_SOURCE_BREAKER.mark_dead(
                src,
                log,
                f"{label}本轮不可达，改用后续备用源" if src != "eastmoney" else "东财日线本轮不可达，不再请求 stock_zh_a_hist / push2his",
            )
    raise RuntimeError(f"{code} 日线全部失败；" + " | ".join(errors))


_NAME_CACHE: dict[str, str] = {}
_PERSIST_LOADED = False
_EM_NAME_DEAD = False
_DATA_ROOT_OVERRIDE: Path | None = None
# 腾讯 v_sz000333="..." 与新浪 var hq_str_sz000333="..." 都取引号内载荷
_QUOTED_PAYLOAD_RE = re.compile(r'="([^"]*)"')
_DOCS_WATCH_SUFFIX = "_自选"


def set_name_data_root(path: Path | None) -> None:
    """测试用：简称本地查找只走该根目录（data/cache、data/docs）。"""
    global _DATA_ROOT_OVERRIDE, _PERSIST_LOADED
    _DATA_ROOT_OVERRIDE = Path(path) if path is not None else None
    _PERSIST_LOADED = False


def reset_name_lookup() -> None:
    """清空进程内简称缓存与东财简称熔断（测试 / 新一轮）。"""
    global _PERSIST_LOADED, _EM_NAME_DEAD
    _NAME_CACHE.clear()
    _PERSIST_LOADED = False
    _EM_NAME_DEAD = False


def remember_name(code: str, name: str) -> str:
    """写入内存缓存；非空名称顺带落到 data/cache/stock_names.json。"""
    code = str(code).zfill(6)
    name = (name or "").strip()
    _NAME_CACHE[code] = name
    if name:
        _persist_name(code, name)
    return name


def resolve_name(code: str, hint: str | None, cfg: dict | None = None, limiter: RateLimiter | None = None) -> str:
    """调用方已有名称则不再出网；否则走 fetch_name。"""
    hint = (hint or "").strip()
    if hint:
        remember_name(code, hint)
        return hint
    return fetch_name(code, cfg or {}, limiter)


def parse_tencent_quote(text: str, code: str = "") -> str:
    """v_sz000333=\"51~美的集团~000333~...\" → 美的集团。"""
    if not text:
        return ""
    match = _QUOTED_PAYLOAD_RE.search(text)
    payload = match.group(1) if match else ""
    if not payload:
        return ""
    parts = payload.split("~")
    if len(parts) < 2:
        return ""
    name = parts[1].strip()
    if not name or name in {"-", "--"}:
        return ""
    return name


def parse_sina_hq(text: str, code: str = "") -> str:
    """var hq_str_sz000333=\"美的集团,...\" → 美的集团。"""
    if not text:
        return ""
    match = _QUOTED_PAYLOAD_RE.search(text)
    payload = (match.group(1) if match else "").strip()
    if not payload:
        return ""
    name = payload.split(",", 1)[0].strip()
    if not name or name in {"-", "--"}:
        return ""
    return name


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path)
        try:
            if path.exists():
                key = str(path.resolve())
        except OSError:
            pass
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _data_roots() -> list[Path]:
    if _DATA_ROOT_OVERRIDE is not None:
        return [_DATA_ROOT_OVERRIDE]
    roots: list[Path] = []
    try:
        from launcher.paths import project_root

        roots.append(project_root())
    except Exception:
        pass
    roots.append(Path.cwd())
    return _unique_paths(roots)


def _name_cache_file() -> Path:
    return _data_roots()[0] / "data" / "cache" / "stock_names.json"


def _szse_stock_files() -> list[Path]:
    files = [root / "data" / "cache" / "szse_stock.json" for root in _data_roots()]
    if _DATA_ROOT_OVERRIDE is None:
        files.append(Path("data/cache/szse_stock.json"))
    return _unique_paths(files)


def _docs_roots() -> list[Path]:
    roots: list[Path] = []
    if _DATA_ROOT_OVERRIDE is not None:
        return [_DATA_ROOT_OVERRIDE / "data" / "docs"]
    try:
        from launcher.paths import docs_dir

        roots.append(docs_dir())
    except Exception:
        pass
    for root in _data_roots():
        roots.append(root / "data" / "docs")
    return _unique_paths(roots)


def _load_persist_cache() -> None:
    global _PERSIST_LOADED
    if _PERSIST_LOADED:
        return
    _PERSIST_LOADED = True
    path = _name_cache_file()
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    for raw_code, raw_name in data.items():
        code = str(raw_code).zfill(6)
        if code in _NAME_CACHE:
            continue
        _NAME_CACHE[code] = str(raw_name or "").strip()


def _persist_name(code: str, name: str) -> None:
    if not name:
        return
    try:
        path = _name_cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        data[code] = name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        return


def _name_from_docs_folder(code: str, folder: str) -> str:
    text = folder.strip()
    if text.endswith(_DOCS_WATCH_SUFFIX):
        text = text[: -len(_DOCS_WATCH_SUFFIX)]
    if text == code:
        return ""
    prefix = f"{code}_"
    if text.startswith(prefix):
        text = text[len(prefix) :]
    if text.endswith(_DOCS_WATCH_SUFFIX):
        text = text[: -len(_DOCS_WATCH_SUFFIX)]
    text = text.strip().strip("_")
    if not text or text == "自选":
        return ""
    return text


def _name_from_docs(code: str) -> str:
    code = str(code).zfill(6)
    for root in _docs_roots():
        if not root.is_dir():
            continue
        try:
            matches = sorted(
                p
                for p in root.iterdir()
                if p.is_dir() and (p.name == code or p.name.startswith(f"{code}_"))
            )
        except OSError:
            continue
        for folder in matches:
            name = _name_from_docs_folder(code, folder.name)
            if name:
                return name
    return ""


def _name_from_cninfo(code: str) -> str:
    files = _szse_stock_files()
    try:
        from stock_screener.datasources.cninfo import peek_stock_name

        hit = peek_stock_name(code, cache_files=files)
        if hit:
            return str(hit).strip()
    except Exception:
        pass
    for path in files:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stocks = (data or {}).get("stockList", []) if isinstance(data, dict) else []
        for item in stocks:
            if not isinstance(item, dict):
                continue
            if str(item.get("code") or "").zfill(6) != code:
                continue
            name = str(item.get("zwjc") or "").strip()
            if name:
                return name
    return ""


def _http_get_text(url: str, timeout: float, headers: dict[str, str] | None = None) -> str:
    from .network import http_get_text

    return http_get_text(url, timeout=timeout, headers=headers)


def _is_em_conn_fail(exc: BaseException) -> bool:
    blob = f"{type(exc).__name__} {exc}".lower()
    return any(
        token in blob
        for token in (
            "curl: (56)",
            "(56)",
            "connection closed",
            "failed to perform",
            "connection aborted",
        )
    )


def _trip_em_name(exc: BaseException) -> None:
    global _EM_NAME_DEAD
    if _EM_NAME_DEAD:
        return
    _EM_NAME_DEAD = True
    log.warning("东财简称本轮不可达，后续不再请求 stock_individual_info_em：%s", exc)


def _fetch_tencent_name(code: str, timeout: float) -> str:
    symbol = _market_symbol(code)
    url = f"https://qt.gtimg.cn/q={symbol}"
    text = _http_get_text(
        url,
        timeout,
        {"Referer": "https://gu.qq.com/", "User-Agent": "Mozilla/5.0"},
    )
    return parse_tencent_quote(text, code)


def _fetch_sina_name(code: str, timeout: float) -> str:
    symbol = _market_symbol(code)
    url = f"https://hq.sinajs.cn/list={symbol}"
    text = _http_get_text(
        url,
        timeout,
        {"Referer": "https://finance.sina.com.cn/", "Host": "hq.sinajs.cn"},
    )
    return parse_sina_hq(text, code)


def _fetch_eastmoney_name(code: str, timeout: float) -> str:
    ak = _import_akshare()
    info = ak.stock_individual_info_em(symbol=code, timeout=timeout)
    if info is None or info.empty:
        raise RuntimeError("个股资料为空")
    cols = {str(c): c for c in info.columns}
    item_col = cols.get("item") or cols.get("指标") or list(info.columns)[0]
    value_col = cols.get("value") or cols.get("值") or list(info.columns)[1]
    for key in ("股票简称", "证券简称", "名称"):
        hit = info.loc[info[item_col].astype(str) == key, value_col]
        if not hit.empty:
            name = str(hit.iloc[0]).strip()
            if name:
                return name
    raise RuntimeError("个股资料中无简称")


def fetch_name(code: str, cfg: dict, limiter: RateLimiter | None = None) -> str:
    """证券简称：内存/本地文件 → 腾讯。失败返回空串。"""
    code = str(code).zfill(6)
    if code in _NAME_CACHE:
        return _NAME_CACHE[code]
    _load_persist_cache()
    if code in _NAME_CACHE:
        return _NAME_CACHE[code]

    local = _name_from_docs(code) or _name_from_cninfo(code)
    if local:
        return remember_name(code, local)

    timeout = min(float(cfg_get(cfg, "network.timeout_seconds", 30)), 8.0)
    try:
        if limiter is not None:
            limiter.wait()
        name = _fetch_tencent_name(code, timeout)
        if name:
            return remember_name(code, name)
    except Exception as exc:
        log.debug("%s 简称源失败：%s", code, exc)

    log.warning("未能获取 %s 名称", code)
    return remember_name(code, "")
