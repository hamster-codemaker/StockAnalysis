"""读取自选股清单：txt / csv，支持 # 注释与空行。"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("tech_analysis")

_CODE_RE = re.compile(r"^\d{6}$")
_HEADER_NAMES = {"代码", "code", "股票代码", "证券代码", "ts_code"}


@dataclass(frozen=True)
class WatchItem:
    code: str
    name: str = ""


def _normalize_code(raw: str) -> str | None:
    text = (raw or "").strip().strip('"').strip("'")
    if "." in text:
        text = text.split(".", 1)[0]
    if text.lower().startswith(("sh", "sz", "bj")):
        text = text[2:]
    return text if _CODE_RE.match(text) else None


def _parse_row(code_raw: str, name_raw: str = "") -> WatchItem | None:
    code = _normalize_code(code_raw)
    if code is None:
        return None
    name = (name_raw or "").strip().strip('"').strip("'")
    return WatchItem(code=code, name=name)


def load_watchlist(path: str | Path) -> list[WatchItem]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"未找到自选股文件：{file_path}")

    items: list[WatchItem] = []
    seen: set[str] = set()
    suffix = file_path.suffix.lower()

    if suffix == ".csv":
        rows = _read_csv(file_path)
    else:
        rows = _read_txt(file_path)

    for code_raw, name_raw in rows:
        item = _parse_row(code_raw, name_raw)
        if item is None:
            preview = f"{code_raw},{name_raw}".strip(",")
            if preview:
                log.warning("忽略无效行：%s", preview)
            continue
        if item.code in seen:
            log.warning("重复代码已跳过：%s", item.code)
            continue
        seen.add(item.code)
        items.append(item)

    return items


def _read_txt(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    text = path.read_text(encoding="utf-8-sig")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "," in line:
            left, right = line.split(",", 1)
            if left.strip().lower() in _HEADER_NAMES:
                continue
            rows.append((left, right))
        else:
            parts = line.split()
            if parts[0].lower() in _HEADER_NAMES:
                continue
            rows.append((parts[0], parts[1] if len(parts) > 1 else ""))
    return rows


def _read_csv(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(f, dialect)
        header_checked = False
        code_idx, name_idx = 0, 1
        for parts in reader:
            if not parts:
                continue
            first = (parts[0] or "").strip()
            if not first or first.startswith("#"):
                continue
            if not header_checked:
                header_checked = True
                lowered = [p.strip().lower() for p in parts]
                if any(p in _HEADER_NAMES or p in {"名称", "name", "股票名称"} for p in [first, *lowered]):
                    for i, p in enumerate(lowered):
                        if p in {x.lower() for x in _HEADER_NAMES}:
                            code_idx = i
                        if p in {"名称", "name", "股票名称"}:
                            name_idx = i
                    continue
            code = parts[code_idx] if code_idx < len(parts) else ""
            name = parts[name_idx] if name_idx < len(parts) else ""
            rows.append((code, name))
    return rows
