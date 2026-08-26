"""统一图形界面：自选股 / 成长股工具 / 技术面 / 定时任务。"""

from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from datetime import datetime
from queue import Empty
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from launcher.daily_report import load_last_report
from launcher.paths import (
    TASK_AUTOSTART,
    TASK_DAILY,
    chdir_project_root,
    ensure_watchlist,
    project_root,
    settings_path,
    suite_argv,
    tech_argv,
    tech_output_dir,
    watchlist_analyze_argv,
    watchlist_output_dir,
    watchlist_path,
)
from launcher.settings import dated_report_dir, load_settings, normalize_daily_time, save_settings
from launcher.tasks import apply_scheduled_tasks, query_task_status

try:
    from app_gui.runner import ProcessRunner
    from app_gui.window import GrowthStockPanel, _enable_dpi
except ImportError:
    sys.path.insert(0, str(project_root() / "app_gui"))
    from runner import ProcessRunner  # type: ignore
    from window import GrowthStockPanel, _enable_dpi  # type: ignore

FONT_UI = ("Microsoft YaHei UI", 10)
FONT_UI_BOLD = ("Microsoft YaHei UI", 11, "bold")
FONT_LOG = ("Consolas", 9)


def format_command(argv: list[str]) -> str:
    parts = []
    for item in argv:
        parts.append(f'"{item}"' if (not item or any(ch.isspace() for ch in item)) else item)
    return " ".join(parts)


class SuiteApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("成长股分析套件")
        self.geometry("1000x760")
        self.minsize(860, 600)
        self.option_add("*Font", FONT_UI)

        self.tech_runner = ProcessRunner()
        self.report_runner = ProcessRunner()
        self.watch_runner = ProcessRunner()
        self._tech_stop = False
        self._report_stop = False
        self._watch_stop = False
        self._loading_settings = False
        self._watchlist_saved = ""

        self.autostart_var = tk.BooleanVar(value=False)
        self.daily_var = tk.BooleanVar(value=False)
        self.time_var = tk.StringVar(value="16:00")
        self.report_dir_var = tk.StringVar(value="")
        self.task_status_var = tk.StringVar(value="")
        self._sync_gen = 0
        self._query_gen = 0
        self.tech_status_var = tk.StringVar(value="就绪")
        self.watch_status_var = tk.StringVar(value="就绪")
        self.tech_verbose = tk.BooleanVar(value=False)
        self.tech_preview = tk.StringVar()

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.nb = nb

        self.watch_tab = ttk.Frame(nb)
        self.growth_tab = GrowthStockPanel(nb)
        self.tech_tab = ttk.Frame(nb)
        self.sched_tab = ttk.Frame(nb)
        nb.add(self.watch_tab, text="自选股")
        nb.add(self.growth_tab, text="成长股工具")
        nb.add(self.tech_tab, text="技术面")
        nb.add(self.sched_tab, text="定时任务")

        self._build_watchlist()
        self._build_tech()
        self._build_schedule()
        self._reload_settings_into_ui()
        self._refresh_tech_preview()
        nb.bind("<<NotebookTabChanged>>", self._on_tab)
        self.after(80, self._poll)
        self.after(400, self._sync_tasks_async)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_watchlist(self) -> None:
        pad = {"padx": 10, "pady": 6}
        ttk.Label(self.watch_tab, text="用户自选股（单一名单）", font=FONT_UI_BOLD).pack(anchor="w", **pad)
        ttk.Label(
            self.watch_tab,
            text="每行 6 位代码，# 注释。与点金术、成长股筛选独立。保存后立刻写 userdata/watchlist.txt。"
            "「分析全部自选股」对名单做技术面 + 基本面 + 综合分析，写入独立「自选股」文件夹，不跑点金术。",
            foreground="#444",
            wraplength=920,
        ).pack(anchor="w", padx=10)
        bar = ttk.Frame(self.watch_tab)
        bar.pack(fill="x", padx=10, pady=4)
        ttk.Button(bar, text="保存", command=self._save_watchlist).pack(side="left")
        ttk.Button(bar, text="重新加载", command=self._load_watchlist).pack(side="left", padx=8)
        self.watch_analyze_btn = ttk.Button(bar, text="分析全部自选股", command=self._start_watchlist_analyze)
        self.watch_analyze_btn.pack(side="left", padx=8)
        self.watch_stop_btn = ttk.Button(bar, text="停止分析", command=self._stop_watchlist_analyze, state="disabled")
        self.watch_stop_btn.pack(side="left")
        ttk.Button(bar, text="打开自选股输出", command=self._open_watchlist_out).pack(side="left", padx=8)
        ttk.Label(bar, textvariable=self.watch_status_var, foreground="#1a5f2a").pack(side="left", padx=8)
        ttk.Label(self.watch_tab, text=str(watchlist_path()), foreground="#555").pack(anchor="w", padx=10)
        self.watch_editor = ScrolledText(self.watch_tab, height=16, wrap="word", font=FONT_LOG)
        self.watch_editor.pack(fill="both", expand=True, padx=10, pady=8)
        log_box = ttk.LabelFrame(self.watch_tab, text="一键分析日志")
        log_box.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.watch_log = ScrolledText(log_box, height=8, wrap="word", font=FONT_LOG, state="disabled")
        self.watch_log.pack(fill="both", expand=True, padx=8, pady=8)
        self._load_watchlist()

    def _build_tech(self) -> None:
        ttk.Label(self.tech_tab, text="自选股技术面分析", font=FONT_UI_BOLD).pack(anchor="w", padx=10, pady=(10, 4))
        ttk.Label(
            self.tech_tab,
            text="子进程调用 tech_analysis，清单固定为 userdata/watchlist.txt。",
            foreground="#444",
        ).pack(anchor="w", padx=10)
        extra = ttk.Frame(self.tech_tab)
        extra.pack(fill="x", padx=10, pady=6)
        ttk.Checkbutton(extra, text="详细日志（-v）", variable=self.tech_verbose, command=self._refresh_tech_preview).pack(side="left")
        actions = ttk.Frame(self.tech_tab)
        actions.pack(fill="x", padx=10, pady=6)
        self.tech_start = ttk.Button(actions, text="开始分析", command=self._start_tech)
        self.tech_start.pack(side="left")
        self.tech_stop = ttk.Button(actions, text="停止", command=self._stop_tech, state="disabled")
        self.tech_stop.pack(side="left", padx=8)
        ttk.Button(actions, text="打开输出目录", command=self._open_tech_out).pack(side="left")
        ttk.Label(actions, textvariable=self.tech_status_var, foreground="#1a5f2a").pack(side="left", padx=16)
        ttk.Label(self.tech_tab, textvariable=self.tech_preview, wraplength=920, foreground="#222").pack(fill="x", padx=10, pady=4)
        box = ttk.LabelFrame(self.tech_tab, text="运行日志")
        box.pack(fill="both", expand=True, padx=10, pady=8)
        self.tech_log = ScrolledText(box, height=16, wrap="word", font=FONT_LOG, state="disabled")
        self.tech_log.pack(fill="both", expand=True, padx=8, pady=8)

    def _build_schedule(self) -> None:
        ttk.Label(self.sched_tab, text="开机自启与每日推送", font=FONT_UI_BOLD).pack(anchor="w", padx=10, pady=(10, 4))
        ttk.Label(
            self.sched_tab,
            text=f"勾选立刻写入 {settings_path()}；计划任务在后台同步，进入本页不会创建或删除任务。",
            foreground="#444",
            wraplength=920,
        ).pack(anchor="w", padx=10)

        box = ttk.LabelFrame(self.sched_tab, text="开关（与 YAML 双向同步）")
        box.pack(fill="x", padx=10, pady=8)
        ttk.Checkbutton(
            box, text="开机自启：登录后最小化到右下角托盘（不弹窗、无黑框）", variable=self.autostart_var, command=self._on_autostart
        ).pack(anchor="w", padx=10, pady=6)
        ttk.Checkbutton(
            box, text="每日更新：到点自动生成日报（不必点开托盘）", variable=self.daily_var, command=self._on_daily
        ).pack(anchor="w", padx=10, pady=6)
        row = ttk.Frame(box)
        row.pack(fill="x", padx=10, pady=6)
        ttk.Label(row, text="日报时刻").pack(side="left")
        ttk.Entry(row, textvariable=self.time_var, width=8).pack(side="left", padx=8)
        ttk.Button(row, text="保存时刻", command=self._on_time).pack(side="left")
        ttk.Label(row, text="任务名：StockAnalysisAutostart / StockAnalysisDaily", foreground="#666").pack(side="left", padx=12)
        path_row = ttk.Frame(box)
        path_row.pack(fill="x", padx=10, pady=6)
        ttk.Label(path_row, text="日报目录").pack(side="left")
        ttk.Entry(path_row, textvariable=self.report_dir_var, width=42).pack(side="left", padx=8)
        ttk.Button(path_row, text="保存目录", command=self._on_report_dir).pack(side="left")
        ttk.Label(box, text="空则默认「桌面/日报集」，其下再按日期建子目录。", foreground="#666").pack(anchor="w", padx=10, pady=(0, 6))

        actions = ttk.Frame(self.sched_tab)
        actions.pack(fill="x", padx=10, pady=6)
        ttk.Button(actions, text="从文件刷新", command=self._reload_settings_into_ui).pack(side="left")
        ttk.Button(actions, text="立即试跑日报", command=self._try_report).pack(side="left", padx=8)
        ttk.Button(actions, text="停止试跑", command=self._stop_report).pack(side="left")
        ttk.Button(actions, text="打开今日日报目录", command=self._open_desktop).pack(side="left", padx=8)
        ttk.Label(self.sched_tab, textvariable=self.task_status_var, wraplength=920).pack(anchor="w", padx=10, pady=6)

        log_box = ttk.LabelFrame(self.sched_tab, text="试跑日志")
        log_box.pack(fill="both", expand=True, padx=10, pady=8)
        self.sched_log = ScrolledText(log_box, height=12, wrap="word", font=FONT_LOG, state="disabled")
        self.sched_log.pack(fill="both", expand=True, padx=8, pady=8)

    def _on_tab(self, _evt=None) -> None:
        title = self.nb.tab(self.nb.select(), "text")
        if title == "自选股":
            return
        if self.watch_editor.get("1.0", "end-1c") != self._watchlist_saved:
            self._save_watchlist(quiet=True)
        if title == "定时任务":
            self._reload_settings_into_ui()

    def _load_watchlist(self) -> None:
        path = ensure_watchlist()
        text = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
        self.watch_editor.delete("1.0", "end")
        self.watch_editor.insert("1.0", text)
        self._watchlist_saved = text

    def _save_watchlist(self, quiet: bool = False) -> bool:
        path = ensure_watchlist()
        try:
            text = self.watch_editor.get("1.0", "end-1c")
            path.write_text(text, encoding="utf-8")
            self._watchlist_saved = text
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return False
        if not quiet:
            self.task_status_var.set(f"已保存自选股 {path}")
        return True

    def _reload_settings_into_ui(self) -> None:
        self._loading_settings = True
        s = load_settings()
        self.autostart_var.set(bool(s.autostart))
        self.daily_var.set(bool(s.daily_update))
        self.time_var.set(s.daily_time)
        self.report_dir_var.set(s.report_dir or "")
        self._loading_settings = False
        self._paint_yaml_status(s)
        self._refresh_task_status_async()

    def _collect_settings_from_ui(self):
        s = load_settings()
        s.autostart = bool(self.autostart_var.get())
        s.daily_update = bool(self.daily_var.get())
        s.daily_time = normalize_daily_time(self.time_var.get(), s.daily_time)
        s.report_dir = (self.report_dir_var.get() or "").strip()
        return s

    def _persist_yaml(self):
        s = self._collect_settings_from_ui()
        save_settings(s)
        self.time_var.set(s.daily_time)
        return s

    def _persist_from_ui(self, sync_tasks: bool = True) -> None:
        s = self._persist_yaml()
        self._paint_yaml_status(s)
        if sync_tasks:
            self._sync_tasks_async()

    def _on_autostart(self) -> None:
        if self._loading_settings:
            return
        self._persist_from_ui(sync_tasks=True)

    def _on_daily(self) -> None:
        if self._loading_settings:
            return
        self._persist_from_ui(sync_tasks=True)

    def _on_time(self) -> None:
        if self._loading_settings:
            return
        self.time_var.set(normalize_daily_time(self.time_var.get()))
        self._persist_from_ui(sync_tasks=True)

    def _on_report_dir(self) -> None:
        if self._loading_settings:
            return
        self._persist_from_ui(sync_tasks=False)
        self.task_status_var.set(
            self.task_status_var.get() + f"\n已写入日报目录配置，实际输出：{dated_report_dir()}"
        )

    def _paint_yaml_status(self, s=None, task_line: str | None = None) -> None:
        s = s or load_settings()
        last = load_last_report()
        today = datetime.now().strftime("%Y%m%d")
        delivered = last.get("date") == today and last.get("ok")
        root = dated_report_dir(today, s)
        line = task_line if task_line is not None else "任务：查询中…"
        self.task_status_var.set(
            f"文件：autostart={s.autostart}  daily_update={s.daily_update}  daily_time={s.daily_time}\n"
            f"日报目录：{root}（report_dir={s.report_dir or '（默认 桌面/日报集）'}）\n"
            f"{line}\n"
            f"今日日报：{'已生成 ' + str(last.get('dir', '')) if delivered else '尚未生成'}"
        )

    def _refresh_task_status_async(self) -> None:
        self._query_gen += 1
        gen = self._query_gen

        def work() -> None:
            try:
                status = query_task_status()
            except Exception as exc:
                status = {"error": str(exc)}
            self.after(0, lambda: self._apply_task_query(status, gen))

        threading.Thread(target=work, daemon=True, name="sa-task-query").start()

    def _apply_task_query(self, status: dict, gen: int) -> None:
        if gen != self._query_gen:
            return
        if status.get("error"):
            line = f"任务：查询失败（{status['error']}）"
        else:
            line = (
                f"任务：{TASK_AUTOSTART}={status.get('autostart_task', '?')}  "
                f"{TASK_DAILY}={status.get('daily_task', '?')}  "
                f"下次日报 {status.get('next_daily', '—')}"
            )
        self._paint_yaml_status(task_line=line)

    def _sync_tasks_async(self) -> None:
        self._sync_gen += 1
        gen = self._sync_gen
        self._paint_yaml_status(task_line="正在同步计划任务…")

        def work() -> None:
            try:
                apply_scheduled_tasks()
                status = query_task_status()
            except Exception as exc:
                status = {"error": str(exc)}
            self.after(0, lambda: self._after_task_sync(status, gen))

        threading.Thread(target=work, daemon=True, name="sa-task-sync").start()

    def _after_task_sync(self, status: dict, gen: int) -> None:
        if gen != self._sync_gen:
            return
        self._apply_task_query(status, self._query_gen)

    def _refresh_task_status(self) -> None:
        """兼容试跑结束回调：只刷新 YAML，任务状态走后台。"""
        self._paint_yaml_status()
        self._refresh_task_status_async()

    def _refresh_tech_preview(self) -> None:
        try:
            argv = tech_argv(["--watchlist", str(watchlist_path())] + (["-v"] if self.tech_verbose.get() else []))
            self.tech_preview.set(format_command(argv))
        except Exception as exc:
            self.tech_preview.set(str(exc))

    def _append(self, widget: ScrolledText, text: str) -> None:
        widget.configure(state="normal")
        widget.insert("end", text + "\n")
        widget.see("end")
        widget.configure(state="disabled")

    def _watchlist_analyze_argv(self) -> list[str]:
        return watchlist_analyze_argv()

    def _start_watchlist_analyze(self) -> None:
        if self.watch_runner.running:
            return
        self._save_watchlist(quiet=True)
        argv = self._watchlist_analyze_argv()
        self._watch_stop = False
        self.watch_analyze_btn.configure(state="disabled")
        self.watch_stop_btn.configure(state="normal")
        self.watch_status_var.set("运行中…")
        self._append(self.watch_log, "─" * 50)
        self._append(self.watch_log, format_command(argv))
        try:
            self.watch_runner.start(argv, project_root())
        except Exception as exc:
            self.watch_analyze_btn.configure(state="normal")
            self.watch_stop_btn.configure(state="disabled")
            messagebox.showerror("启动失败", str(exc))

    def _stop_watchlist_analyze(self) -> None:
        self._watch_stop = True
        self.watch_runner.stop()

    def _open_watchlist_out(self) -> None:
        out = watchlist_output_dir()
        out.mkdir(parents=True, exist_ok=True)
        os.startfile(str(out))

    def _start_tech(self) -> None:
        if self.tech_runner.running:
            return
        self._save_watchlist(quiet=True)
        argv = tech_argv(["--watchlist", str(watchlist_path())] + (["-v"] if self.tech_verbose.get() else []))
        self._tech_stop = False
        self.tech_start.configure(state="disabled")
        self.tech_stop.configure(state="normal")
        self.tech_status_var.set("运行中…")
        self._append(self.tech_log, "─" * 50)
        self._append(self.tech_log, format_command(argv))
        try:
            self.tech_runner.start(argv, project_root())
        except Exception as exc:
            self.tech_start.configure(state="normal")
            self.tech_stop.configure(state="disabled")
            messagebox.showerror("启动失败", str(exc))

    def _stop_tech(self) -> None:
        self._tech_stop = True
        self.tech_runner.stop()

    def _try_report(self) -> None:
        if self.report_runner.running:
            return
        self._save_watchlist(quiet=True)
        s = load_settings()
        if not s.daily_update:
            if not messagebox.askyesno("每日更新已关闭", "settings.yaml 中 daily_update=false。仍要强制试跑一次吗？（不会改开关）"):
                return
            argv = suite_argv("--daily-report") + ["--force"]
        else:
            argv = suite_argv("--daily-report")
        self._report_stop = False
        self._append(self.sched_log, "─" * 50)
        self._append(self.sched_log, format_command(argv))
        try:
            self.report_runner.start(argv, project_root())
        except Exception as exc:
            messagebox.showerror("启动失败", str(exc))

    def _stop_report(self) -> None:
        self._report_stop = True
        self.report_runner.stop()

    def _open_tech_out(self) -> None:
        out = tech_output_dir()
        out.mkdir(parents=True, exist_ok=True)
        os.startfile(str(out))

    def _open_desktop(self) -> None:
        folder = dated_report_dir()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    def _poll(self) -> None:
        while True:
            try:
                kind, payload = self.tech_runner.events.get_nowait()
            except Empty:
                break
            if kind == "log":
                self._append(self.tech_log, str(payload))
            elif kind == "done":
                self.tech_start.configure(state="normal")
                self.tech_stop.configure(state="disabled")
                self.tech_status_var.set(f"结束 {payload}")
        while True:
            try:
                kind, payload = self.report_runner.events.get_nowait()
            except Empty:
                break
            if kind == "log":
                self._append(self.sched_log, str(payload))
            elif kind == "done":
                self._refresh_task_status()
                self._append(self.sched_log, f">>> 结束 {payload}")
        while True:
            try:
                kind, payload = self.watch_runner.events.get_nowait()
            except Empty:
                break
            if kind == "log":
                self._append(self.watch_log, str(payload))
            elif kind == "done":
                self.watch_analyze_btn.configure(state="normal")
                self.watch_stop_btn.configure(state="disabled")
                self.watch_status_var.set(f"结束 {payload}")
                self._append(self.watch_log, f">>> 结束 {payload}")
        self.after(80, self._poll)

    def _on_close(self) -> None:
        if (
            self.growth_tab.runner.running
            or self.tech_runner.running
            or self.report_runner.running
            or self.watch_runner.running
        ):
            if not messagebox.askyesno("退出", "任务仍在运行，确定结束并退出？"):
                return
            self.growth_tab.runner.stop()
            self.tech_runner.stop()
            self.report_runner.stop()
            self.watch_runner.stop()
        if self.watch_editor.get("1.0", "end-1c") != self._watchlist_saved:
            self._save_watchlist(quiet=True)
        self.destroy()


def self_test() -> int:
    chdir_project_root()
    app = SuiteApp()
    app.update_idletasks()
    app.update()
    print(f"WINDOW_OK title={app.title()!r}")
    app.destroy()
    return 0


def main() -> int:
    chdir_project_root()
    _enable_dpi()
    SuiteApp().mainloop()
    return 0
