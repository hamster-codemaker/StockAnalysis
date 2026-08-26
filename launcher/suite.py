"""统一入口调度：无参数开 GUI；成长股/技术面子命令转发。"""

from __future__ import annotations

import logging
import os
import sys

from launcher.paths import chdir_project_root, ensure_watchlist, frozen

HELP = """StockAnalysis — 成长股文档 + 技术面 + 点金术 统一工具

无参数 / --gui          打开统一图形界面（无控制台黑框）
--daily-report          按 userdata/settings.yaml 生成日报（自选股文件夹 + 点金术全市场，无窗口）
--watchlist-analyze     一键分析全部自选股（技术面+基本面+综合分析），不跑点金术
watchlist               同上
--daemon                登录后托盘静默：右下角图标，到点自动出日报，不必点开
--self-test             GUI 自检后退出
--help                  显示本说明

成长股 CLI（与 python main.py 相同）：
  screen | download | policies | update-policies | finance | run | dianjin
  download/finance/policies 可用 --watchlist userdata/watchlist.txt
  也可继续用 --codes / --screening-file
  dianjin 为全市场扫描，不把自选股当股票池；命中则目录标 _自选
  --hist-limit / --limit / --screen-only 仅测试用

技术面：
  tech [--watchlist PATH] [-v]
  120 个交易日 K 线 + MA8/24/120、BOLL(20,2)、MACD(12,26,9)、RSI(6/12/24)

开关（单一真相 = userdata/settings.yaml，每次运行重读）：
  autostart       开机自启任务 StockAnalysisAutostart（--daemon 托盘）
  daily_update    每日任务 StockAnalysisDaily；托盘进程内也会到点出日报
  daily_time      默认 16:00

自选股：userdata/watchlist.txt（与 data/screening/、data/dianjin/ 均独立）
自选股输出：data/watchlist/ 与 桌面/日报集/<日期>/自选股/（个股含综合分析.md）
日报目录：桌面/日报集/YYYYMMDD/（settings.yaml 的 report_dir 可改）
点金术：桌面/日报集/<日期>/点金术/ 与 点金术extra/ ，源数据 data/dianjin/<日期>/
"""

_SILENT_FLAGS = {"--gui", "--daemon", "--daily-report", "--self-test"}


def _want_console(args: list[str]) -> bool:
    if not args:
        return False
    return args[0] not in _SILENT_FLAGS


def _attach_console() -> None:
    """windowed exe 在 --help / CLI 时补一个控制台，便于看输出。"""
    if sys.platform != "win32" or not frozen():
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        if kernel32.GetConsoleWindow():
            return
        attached = kernel32.AttachConsole(-1)
        if attached == 0:
            kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        try:
            sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
        except OSError:
            pass
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    os.environ.setdefault("MPLBACKEND", "Agg")
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    chdir_project_root()
    ensure_watchlist()
    args = list(sys.argv[1:] if argv is None else argv)
    if _want_console(args):
        _attach_console()

    if not args or args[0] in ("--gui",):
        from launcher.gui import main as gui_main

        return gui_main()
    if args[0] in ("-h", "--help"):
        print(HELP)
        return 0
    if args[0] == "--self-test":
        from launcher.gui import self_test

        return self_test()
    if args[0] == "--daily-report":
        from launcher.daily_report import main as daily_main

        return daily_main(args)
    if args[0] in ("--watchlist-analyze", "watchlist"):
        from launcher.watchlist_report import main as watch_main

        return watch_main(args)
    if args[0] == "--daemon":
        from launcher.scheduler import main as daemon_main

        return daemon_main()
    if args[0] in ("tech", "tech-analysis"):
        sys.argv = [sys.argv[0], *args[1:]]
        from tech_analysis.main import main as tech_main

        return tech_main()

    logging.getLogger("launcher").debug("转发成长股 CLI：%s", args)
    sys.argv = [sys.argv[0], *args]
    from main import main as growth_main

    return growth_main()
