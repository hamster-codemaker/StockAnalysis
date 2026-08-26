"""项目根与用户数据路径。frozen 时以 exe 所在目录为准（不是 _internal）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

TASK_AUTOSTART = "StockAnalysisAutostart"
TASK_DAILY = "StockAnalysisDaily"


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    if frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundled_root() -> Path:
    if frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return project_root()


def userdata_dir() -> Path:
    return project_root() / "userdata"


def watchlist_path() -> Path:
    return userdata_dir() / "watchlist.txt"


def settings_path() -> Path:
    return userdata_dir() / "settings.yaml"


def last_report_path() -> Path:
    return userdata_dir() / "last_report.json"


def tech_output_dir() -> Path:
    return project_root() / "data" / "tech_analysis"


def watchlist_output_dir() -> Path:
    """自选股一键/日报分析的独立输出：data/watchlist。与点金术 data/dianjin 分开。"""
    return project_root() / "data" / "watchlist"


def docs_dir() -> Path:
    return project_root() / "data" / "docs"


def screening_dir() -> Path:
    return project_root() / "data" / "screening"


def dianjin_dir(date_str: str | None = None) -> Path:
    """点金术当日输出：data/dianjin/YYYYMMDD。与自选股、成长股筛选目录分开。"""
    from datetime import datetime

    stamp = date_str or datetime.now().strftime("%Y%m%d")
    return project_root() / "data" / "dianjin" / stamp


def config_path() -> Path:
    return project_root() / "config.yaml"


def entry_script() -> Path:
    return project_root() / "entry.py"


def chdir_project_root() -> Path:
    root = project_root()
    os.chdir(str(root))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def ensure_watchlist() -> Path:
    """保证 userdata/watchlist.txt 存在。首次可从 tech_analysis/watchlist.txt 复制。"""
    dest = watchlist_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return dest
    seed = project_root() / "tech_analysis" / "watchlist.txt"
    if not seed.is_file():
        seed = bundled_root() / "tech_analysis" / "watchlist.txt"
    if seed.is_file():
        dest.write_text(seed.read_text(encoding="utf-8-sig"), encoding="utf-8")
        return dest
    dest.write_text(
        "# 用户自选股（单一真相来源）\n"
        "# 格式：每行一个 6 位股票代码；# 开头为注释，空行忽略\n"
        "# 示例：600900,长江电力\n",
        encoding="utf-8",
    )
    return dest


def windows_desktop() -> Path:
    """当前用户桌面（含 OneDrive 重定向），等价于 [Environment]::GetFolderPath('Desktop')。"""
    if sys.platform == "win32":
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(260)
            # CSIDL_DESKTOPDIRECTORY = 0x10
            hr = ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)
            if hr == 0 and buf.value:
                return Path(buf.value)
        except Exception:
            pass
        known = os.environ.get("USERPROFILE", "")
        if known:
            return Path(known) / "Desktop"
    return Path.home() / "Desktop"


def _windowless_python(exe: Path) -> Path:
    if exe.name.lower() == "python.exe":
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.is_file():
            return pythonw
    return exe


def launch_command(*, windowless: bool = False) -> tuple[str, str]:
    """计划任务用的 (可执行文件, 参数前缀)。日报再追加 --daily-report。"""
    if frozen():
        return str(Path(sys.executable).resolve()), ""
    exe = Path(sys.executable).resolve()
    if windowless:
        exe = _windowless_python(exe)
    return str(exe), str(entry_script())


def growth_argv(sub_args: list[str], python: str | None = None) -> list[str]:
    """拼装成长股 CLI。frozen 时直接调本 exe。"""
    exe = python or sys.executable
    if frozen():
        return [exe, *sub_args]
    return [exe, str(project_root() / "main.py"), *sub_args]


def tech_argv(extra: list[str] | None = None, python: str | None = None) -> list[str]:
    extra = extra or []
    exe = python or sys.executable
    if frozen():
        return [exe, "tech", *extra]
    return [exe, str(project_root() / "tech_analysis" / "main.py"), *extra]


def suite_argv(flag: str, python: str | None = None) -> list[str]:
    exe = python or sys.executable
    if frozen():
        return [exe, flag]
    return [exe, str(entry_script()), flag]


WATCHLIST_ANALYZE_FLAG = "--watchlist-analyze"


def watchlist_analyze_argv(python: str | None = None) -> list[str]:
    """GUI / 计划外一键：分析全部自选股（不跑点金术）。"""
    return suite_argv(WATCHLIST_ANALYZE_FLAG, python=python)
