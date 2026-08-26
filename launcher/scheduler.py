"""登录后静默守护：托盘常驻，不弹主窗口；到点自动出日报，不必点开托盘。"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from launcher.paths import chdir_project_root, project_root, suite_argv, userdata_dir
from launcher.settings import load_settings, normalize_daily_time

log = logging.getLogger("launcher")

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
_DETACHED = 0x00000008
_NEW_GROUP = 0x00000200
_stop = threading.Event()


def _setup_logging() -> Path:
    log_path = userdata_dir() / "daemon.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        force=True,
    )
    return log_path


def _tray_image():
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((4, 4, 60, 60), radius=12, fill="#1a5f2a")
    draw.line([(14, 44), (26, 30), (36, 38), (50, 16)], fill="#e8f5e9", width=4)
    return img


def _open_gui(_icon=None, _item=None) -> None:
    argv = suite_argv("--gui")
    flags = 0
    if sys.platform == "win32":
        flags = _DETACHED | _NEW_GROUP | _CREATE_NO_WINDOW
    try:
        subprocess.Popen(argv, cwd=str(project_root()), creationflags=flags, close_fds=True)
        log.info("已拉起主界面：%s", " ".join(argv))
    except Exception:
        log.exception("打开主界面失败")


def _run_silent_update() -> None:
    try:
        from launcher.daily_report import run_silent_tech_refresh

        code = run_silent_tech_refresh()
        log.info("静默技术面更新结束，返回码 %s", code)
    except Exception:
        log.exception("静默更新失败（仅记日志，不弹窗）")


def _run_daily_now(*, force: bool = False) -> None:
    try:
        from launcher.daily_report import report_done_today, run_daily_report

        settings = load_settings()
        if not settings.daily_update and not force:
            log.info("配置 daily_update=false，跳过自动日报")
            return
        if report_done_today() and not force:
            log.info("今日日报已生成，跳过重复任务")
            return
        code = run_daily_report()
        log.info("自动日报结束，返回码 %s", code)
    except Exception:
        log.exception("自动日报失败（仅记日志，不弹窗）")


def _seconds_until(hhmm: str) -> float:
    hour, minute = (int(p) for p in normalize_daily_time(hhmm).split(":")[:2])
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def _daily_loop() -> None:
    """托盘进程内定时：到点出日报，用户从未点开托盘也会执行。每次循环重读 YAML。"""
    log.info("日报定时线程已启动")
    while not _stop.is_set():
        settings = load_settings()
        if not settings.daily_update:
            _stop.wait(30)
            continue
        wait = _seconds_until(settings.daily_time)
        log.info("距离下次日报约 %.0f 秒（%s）", wait, settings.daily_time)
        deadline = time.monotonic() + wait
        while not _stop.is_set() and time.monotonic() < deadline:
            _stop.wait(min(20.0, deadline - time.monotonic()))
            latest = load_settings()
            if not latest.daily_update:
                break
            if normalize_daily_time(latest.daily_time) != normalize_daily_time(settings.daily_time):
                break
        else:
            if not _stop.is_set() and load_settings().daily_update:
                _run_daily_now()


def _update_now(_icon=None, _item=None) -> None:
    threading.Thread(target=_run_silent_update, daemon=True, name="sa-tray-update").start()


def _report_now(_icon=None, _item=None) -> None:
    threading.Thread(target=lambda: _run_daily_now(force=True), daemon=True, name="sa-tray-report").start()


def _quit(icon, _item=None) -> None:
    log.info("托盘退出")
    _stop.set()
    icon.stop()


def _start_background_jobs() -> None:
    try:
        from launcher.tasks import apply_scheduled_tasks

        apply_scheduled_tasks()
    except Exception:
        log.exception("同步计划任务失败")
    threading.Thread(target=_daily_loop, daemon=True, name="sa-daily-loop").start()
    settings = load_settings()
    if settings.daily_update:
        threading.Thread(target=_run_silent_update, daemon=True, name="sa-boot-update").start()
    else:
        log.info("配置 daily_update=false，开机不刷技术面")


def _run_tray() -> int:
    import pystray

    menu = pystray.Menu(
        pystray.MenuItem("打开主界面", _open_gui, default=True),
        pystray.MenuItem("立即生成今日日报", _report_now),
        pystray.MenuItem("立即刷新技术面", _update_now),
        pystray.MenuItem("退出", _quit),
    )
    icon = pystray.Icon("StockAnalysis", _tray_image(), "成长股分析套件", menu)
    _start_background_jobs()
    icon.run()
    return 0


def _run_hidden_tk() -> int:
    """无 pystray 时：隐藏 Tk，不显示主窗口，进程仍常驻。"""
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    root.title("StockAnalysisDaemon")
    _start_background_jobs()
    root.mainloop()
    return 0


def main() -> int:
    chdir_project_root()
    _setup_logging()
    log.info("daemon 启动（托盘、无主窗口、无控制台）")
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        log.warning("未安装 pystray/pillow，改为隐藏窗口常驻")
        return _run_hidden_tk()
    return _run_tray()
