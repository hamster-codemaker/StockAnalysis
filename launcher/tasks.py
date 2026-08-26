"""Windows 计划任务：始终先读磁盘上的 settings.yaml 再决定创建或删除。"""

from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from launcher.paths import TASK_AUTOSTART, TASK_DAILY, launch_command, project_root
from launcher.settings import load_settings, normalize_daily_time

log = logging.getLogger("launcher")

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _run_ps(script: str) -> subprocess.CompletedProcess[str]:
    handle, raw = tempfile.mkstemp(suffix=".ps1", prefix="sa_task_")
    path = Path(raw)
    try:
        import os

        os.close(handle)
        path.write_text("\ufeff" + script, encoding="utf-8")
        return subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
    finally:
        path.unlink(missing_ok=True)


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def task_exists(name: str) -> bool:
    proc = subprocess.run(
        ["schtasks", "/Query", "/TN", name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW,
    )
    return proc.returncode == 0


def delete_task(name: str) -> bool:
    if not task_exists(name):
        return True
    proc = _run_ps(
        f"Unregister-ScheduledTask -TaskName {_ps_quote(name)} -Confirm:$false -ErrorAction SilentlyContinue"
    )
    if proc.returncode != 0:
        log.warning("删除计划任务失败 %s：%s", name, (proc.stderr or proc.stdout or "").strip())
        return False
    log.info("已删除计划任务：%s", name)
    return True


def query_task_status() -> dict[str, str]:
    """后台线程用：查计划任务是否已注册。会跑 schtasks / PowerShell。"""
    settings = load_settings()
    auto = "存在" if task_exists(TASK_AUTOSTART) else "无"
    daily = "存在" if task_exists(TASK_DAILY) else "无"
    nxt = next_run_time(TASK_DAILY) if settings.daily_update else "—"
    return {
        "autostart_task": auto,
        "daily_task": daily,
        "next_daily": nxt or "—",
        "autostart": str(settings.autostart),
        "daily_update": str(settings.daily_update),
        "daily_time": settings.daily_time,
    }


def _register(name: str, arguments: str, trigger_ps: str, *, windowless: bool = False) -> bool:
    exe, prefix = launch_command(windowless=windowless)
    if prefix:
        arg = f'"{prefix}" {arguments}'.strip()
    else:
        arg = arguments
    wd = str(project_root())
    script = f"""
$action = New-ScheduledTaskAction -Execute {_ps_quote(exe)} -Argument {_ps_quote(arg)} -WorkingDirectory {_ps_quote(wd)}
$trigger = {trigger_ps}
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName {_ps_quote(name)} -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
"""
    proc = _run_ps(script)
    if proc.returncode != 0:
        log.warning("创建计划任务失败 %s：%s", name, (proc.stderr or proc.stdout or "").strip())
        return False
    log.info("已注册计划任务：%s（工作目录 %s）", name, wd)
    return True


def create_autostart_task() -> bool:
    return _register(
        TASK_AUTOSTART,
        "--daemon",
        "New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME",
        windowless=True,
    )


def create_daily_task(daily_time: str) -> bool:
    hhmm = normalize_daily_time(daily_time)
    return _register(
        TASK_DAILY,
        "--daily-report",
        f"New-ScheduledTaskTrigger -Daily -At {_ps_quote(hhmm)}",
        windowless=True,
    )


def next_run_time(name: str) -> str:
    proc = _run_ps(
        f"""
$info = Get-ScheduledTaskInfo -TaskName {_ps_quote(name)} -ErrorAction SilentlyContinue
if ($null -eq $info) {{ '' }} else {{ $info.NextRunTime.ToString('yyyy-MM-dd HH:mm') }}
"""
    )
    return (proc.stdout or "").strip()


def apply_scheduled_tasks() -> dict[str, str]:
    """按磁盘配置同步任务：false 则删除，true 则创建/覆盖。"""
    settings = load_settings()
    status: dict[str, str] = {}
    if settings.autostart:
        status[TASK_AUTOSTART] = "已启用" if create_autostart_task() else "启用失败"
    else:
        status[TASK_AUTOSTART] = "已关闭" if delete_task(TASK_AUTOSTART) else "关闭失败"
    if settings.daily_update:
        status[TASK_DAILY] = "已启用" if create_daily_task(settings.daily_time) else "启用失败"
    else:
        status[TASK_DAILY] = "已关闭" if delete_task(TASK_DAILY) else "关闭失败"
    nxt = next_run_time(TASK_DAILY) if settings.daily_update else "—"
    status["next_daily"] = nxt or "—"
    status["autostart"] = str(settings.autostart)
    status["daily_update"] = str(settings.daily_update)
    status["daily_time"] = settings.daily_time
    return status
