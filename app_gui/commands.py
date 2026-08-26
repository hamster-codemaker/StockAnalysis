"""根据界面选项拼装 `python main.py …` 参数，不导入业务模块。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return APP_DIR.parent


PROJECT_ROOT = _project_root()
MAIN_PY = PROJECT_ROOT / "main.py"

# (CLI 子命令, 界面标签)
TASKS: list[tuple[str, str]] = [
    ("screen", "筛选"),
    ("download", "下载"),
    ("policies", "政策"),
    ("update-policies", "更新政策"),
    ("finance", "财务分析"),
    ("run", "全流程"),
    ("dianjin", "点金术"),
]

TASK_HINTS: dict[str, str] = {
    "screen": "仅做量化筛选，结果写入 data/screening（CSV + Markdown）。",
    "download": "下载招股书 / 财报 / 研报。可指定代码、自选股，或读取筛选结果 CSV。",
    "policies": "按行业下载政策并刷新相关个股索引（仅自选股 + 已有财报/研报的股票）。",
    "update-policies": "刷新政策库新文件，并重写相关个股政策索引。",
    "finance": "提取历年财报关键数据并出图（不解析扫描版 PDF）。可指定自选股。",
    "run": "筛选 + 下载全流程（个股文档完成后会按配置自动做财务分析）。",
    "dianjin": "点金术 / extra：全 A 股估值快照 → 股息+PE → 仅对幸存者拉 MA120。与自选股名单分开；命中且在自选股中的只做 `_自选` 标注。界面里的数量上限仅手工试跑；日报从不带 hist-limit。",
}

# 各子命令实际接受的可选参数（与 main.py argparse 对齐）
TASK_OPTIONS: dict[str, frozenset[str]] = {
    "screen": frozenset(),
    "download": frozenset({"codes", "screening_file", "limit", "watchlist"}),
    "policies": frozenset({"industries", "screening_file", "max", "limit", "watchlist"}),
    "update-policies": frozenset({"industries", "screening_file", "max", "limit", "watchlist"}),
    "finance": frozenset({"codes", "screening_file", "limit", "watchlist"}),
    "run": frozenset({"limit"}),
    "dianjin": frozenset({"codes", "limit"}),
}

OUTPUT_DIRS: list[tuple[str, str]] = [
    ("文档", "data/docs"),
    ("政策", "data/policies"),
    ("筛选结果", "data/screening"),
    ("点金术", "data/dianjin"),
]


@dataclass
class GuiOptions:
    command: str
    codes: str = ""
    industries: str = ""
    screening_file: str = ""
    limit: str = ""
    per_industry_max: str = ""
    config: str = ""
    verbose: bool = False
    watchlist: str = ""


def split_csv_field(raw: str) -> str:
    """把中英文逗号都当成分隔符，去掉空白项。"""
    text = (raw or "").replace("，", ",")
    parts = [p.strip() for p in text.split(",") if p.strip()]
    return ",".join(parts)


def _positive_int(raw: str, label: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError(f"{label}必须是正整数") from exc
    if value <= 0:
        raise ValueError(f"{label}必须是正整数")
    return str(value)


def build_argv(opts: GuiOptions, python: str | None = None) -> list[str]:
    """拼装子进程参数列表。工作目录必须是项目根，由调用方设置。"""
    if opts.command not in TASK_OPTIONS:
        raise ValueError(f"未知任务：{opts.command}")
    if getattr(sys, "frozen", False):
        argv = [python or sys.executable]
    else:
        if not MAIN_PY.is_file():
            raise FileNotFoundError(f"找不到入口脚本：{MAIN_PY}")
        argv = [python or sys.executable, str(MAIN_PY)]
    if opts.verbose:
        argv.append("-v")

    config = (opts.config or "").strip()
    if config:
        argv.extend(["--config", config])

    argv.append(opts.command)
    allowed = TASK_OPTIONS[opts.command]

    if "watchlist" in allowed:
        watchlist = (opts.watchlist or "").strip()
        if watchlist:
            argv.extend(["--watchlist", watchlist])
    if "codes" in allowed:
        codes = split_csv_field(opts.codes)
        if codes:
            argv.extend(["--codes", codes])
    if "industries" in allowed:
        industries = split_csv_field(opts.industries)
        if industries:
            argv.extend(["--industries", industries])
    if "screening_file" in allowed:
        screening = (opts.screening_file or "").strip()
        if screening:
            argv.extend(["--screening-file", screening])
    if "limit" in allowed:
        limit = _positive_int(opts.limit, "数量上限")
        if limit:
            argv.extend(["--limit", limit])
    if "max" in allowed:
        per_max = _positive_int(opts.per_industry_max, "每行业政策上限")
        if per_max:
            argv.extend(["--max", per_max])
    return argv


def format_command(argv: list[str]) -> str:
    """把参数列表格式化成可复制的一行命令。"""
    parts: list[str] = []
    for item in argv:
        if any(ch.isspace() for ch in item) or not item:
            parts.append(f'"{item}"')
        else:
            parts.append(item)
    return " ".join(parts)
