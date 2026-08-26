"""成长股文档工具主窗口（tkinter，中文）。可独立运行，也可嵌入统一 GUI。"""

from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path
from queue import Empty
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

try:
    from app_gui.commands import (
        OUTPUT_DIRS,
        PROJECT_ROOT,
        TASK_HINTS,
        TASK_OPTIONS,
        TASKS,
        GuiOptions,
        build_argv,
        format_command,
    )
    from app_gui.runner import ProcessRunner
except ImportError:
    from commands import (
        OUTPUT_DIRS,
        PROJECT_ROOT,
        TASK_HINTS,
        TASK_OPTIONS,
        TASKS,
        GuiOptions,
        build_argv,
        format_command,
    )
    from runner import ProcessRunner

FONT_UI = ("Microsoft YaHei UI", 10)
FONT_UI_BOLD = ("Microsoft YaHei UI", 11, "bold")
FONT_LOG = ("Consolas", 9)


def _enable_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _watchlist_file() -> Path:
    try:
        from launcher.paths import ensure_watchlist

        return ensure_watchlist()
    except Exception:
        return PROJECT_ROOT / "userdata" / "watchlist.txt"


def open_directory(rel_or_abs: str) -> None:
    path = Path(rel_or_abs)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(str(path))


class GrowthStockPanel(ttk.Frame):
    def __init__(self, master) -> None:
        super().__init__(master)
        self.runner = ProcessRunner()
        self._stop_requested = False

        self.command_var = tk.StringVar(value="screen")
        self.codes_var = tk.StringVar()
        self.industries_var = tk.StringVar()
        self.screening_var = tk.StringVar()
        self.limit_var = tk.StringVar()
        self.max_var = tk.StringVar()
        self.config_var = tk.StringVar(value="config.yaml")
        self.verbose_var = tk.BooleanVar(value=False)
        self.use_watchlist_var = tk.BooleanVar(value=False)
        self.preview_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就绪")

        self._entries: dict[str, ttk.Entry] = {}
        self._build()
        self.command_var.trace_add("write", lambda *_: self._on_task_change())
        for var in (
            self.codes_var,
            self.industries_var,
            self.screening_var,
            self.limit_var,
            self.max_var,
            self.config_var,
            self.verbose_var,
            self.use_watchlist_var,
        ):
            var.trace_add("write", lambda *_: self._refresh_preview())
        self._on_task_change()
        self.after(80, self._poll_events)

    def _build(self) -> None:
        try:
            self.winfo_toplevel().option_add("*Font", FONT_UI)
        except Exception:
            pass
        pad = {"padx": 10, "pady": 6}

        header = ttk.Frame(self)
        header.pack(fill="x", **pad)
        ttk.Label(header, text="成长股文档 / 政策 / 财务 / 点金术", font=FONT_UI_BOLD).pack(anchor="w")
        ttk.Label(
            header,
            text="子进程调用 main.py。自选股、成长股筛选、点金术三套名单互不混用；点金术是全市场扫描。",
            foreground="#444",
        ).pack(anchor="w")

        task_box = ttk.LabelFrame(self, text="任务")
        task_box.pack(fill="x", padx=10, pady=(0, 6))
        radios = ttk.Frame(task_box)
        radios.pack(fill="x", padx=8, pady=6)
        for i, chunk in enumerate((TASKS[:4], TASKS[4:])):
            row = ttk.Frame(radios)
            row.pack(fill="x", pady=(0 if i == 0 else 4, 0))
            for value, label in chunk:
                ttk.Radiobutton(
                    row, text=label, value=value, variable=self.command_var
                ).pack(side="left", padx=(0, 12))
        self.hint_label = ttk.Label(task_box, text="", foreground="#333", wraplength=860)
        self.hint_label.pack(fill="x", padx=8, pady=(0, 8))

        opts = ttk.LabelFrame(self, text="参数")
        opts.pack(fill="x", padx=10, pady=(0, 6))
        opts.columnconfigure(1, weight=1)

        self._add_entry(opts, 0, "codes", "股票代码", self.codes_var, "逗号分隔；填写则优先于自选股/筛选")
        self._add_entry(opts, 1, "industries", "行业关键词", self.industries_var, "逗号分隔，如 集成电路,通用设备")
        self._add_file_row(opts, 2, "screening_file", "筛选结果 CSV", self.screening_var, self._browse_csv)
        self._add_entry(opts, 3, "limit", "数量上限", self.limit_var, "最多处理的股票或行业数")
        self._add_entry(opts, 4, "max", "每行业政策上限", self.max_var, "覆盖配置中的 per_industry_max")
        self._add_file_row(opts, 5, "config", "配置文件", self.config_var, self._browse_config)

        extra = ttk.Frame(opts)
        extra.grid(row=6, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 8))
        ttk.Checkbutton(extra, text="详细日志（-v）", variable=self.verbose_var).pack(side="left")
        ttk.Checkbutton(
            extra, text="使用自选股（userdata/watchlist.txt）", variable=self.use_watchlist_var
        ).pack(side="left", padx=(16, 0))

        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=10, pady=(0, 6))
        self.start_btn = ttk.Button(actions, text="开始", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(actions, text="停止", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        ttk.Label(actions, textvariable=self.status_var, foreground="#1a5f2a").pack(
            side="left", padx=16
        )

        dirs = ttk.LabelFrame(self, text="打开输出目录")
        dirs.pack(fill="x", padx=10, pady=(0, 6))
        dir_row = ttk.Frame(dirs)
        dir_row.pack(fill="x", padx=8, pady=6)
        for label, rel in OUTPUT_DIRS:
            ttk.Button(
                dir_row, text=label, command=lambda p=rel: self._open_dir(p)
            ).pack(side="left", padx=(0, 8))

        preview = ttk.LabelFrame(self, text="将执行的命令")
        preview.pack(fill="x", padx=10, pady=(0, 6))
        ttk.Label(
            preview, textvariable=self.preview_var, wraplength=860, foreground="#222"
        ).pack(fill="x", padx=8, pady=6)

        log_box = ttk.LabelFrame(self, text="运行日志")
        log_box.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        log_bar = ttk.Frame(log_box)
        log_bar.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Button(log_bar, text="清空日志", command=self._clear_log).pack(side="right")
        self.log = ScrolledText(
            log_box, height=16, wrap="word", font=FONT_LOG, state="disabled"
        )
        self.log.pack(fill="both", expand=True, padx=8, pady=8)

    def _add_entry(self, parent, row, key, label, variable, hint) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="e", padx=(8, 6), pady=4)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Label(parent, text=hint, foreground="#666").grid(row=row, column=2, sticky="w", padx=(8, 8))
        self._entries[key] = entry

    def _add_file_row(self, parent, row, key, label, variable, browse) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="e", padx=(8, 6), pady=4)
        mid = ttk.Frame(parent)
        mid.grid(row=row, column=1, sticky="ew", pady=4)
        mid.columnconfigure(0, weight=1)
        entry = ttk.Entry(mid, textvariable=variable)
        entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(mid, text="浏览…", command=browse).grid(row=0, column=1, padx=(6, 0))
        self._entries[key] = entry

    def _browse_csv(self) -> None:
        initial = PROJECT_ROOT / "data" / "screening"
        if not initial.is_dir():
            initial = PROJECT_ROOT
        path = filedialog.askopenfilename(
            title="选择筛选结果 CSV",
            initialdir=str(initial),
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if path:
            self.screening_var.set(path)

    def _browse_config(self) -> None:
        path = filedialog.askopenfilename(
            title="选择配置文件",
            initialdir=str(PROJECT_ROOT),
            filetypes=[("YAML", "*.yaml;*.yml"), ("所有文件", "*.*")],
        )
        if path:
            self.config_var.set(path)

    def _current_options(self) -> GuiOptions:
        watchlist = ""
        if self.use_watchlist_var.get() and "watchlist" in TASK_OPTIONS.get(self.command_var.get(), frozenset()):
            watchlist = str(_watchlist_file())
        return GuiOptions(
            command=self.command_var.get(),
            codes=self.codes_var.get(),
            industries=self.industries_var.get(),
            screening_file=self.screening_var.get(),
            limit=self.limit_var.get(),
            per_industry_max=self.max_var.get(),
            config=self.config_var.get(),
            verbose=bool(self.verbose_var.get()),
            watchlist=watchlist,
        )

    def _on_task_change(self) -> None:
        command = self.command_var.get()
        allowed = TASK_OPTIONS.get(command, frozenset())
        self.hint_label.configure(text=TASK_HINTS.get(command, ""))
        for key, entry in self._entries.items():
            if key == "config":
                entry.configure(state="normal")
                continue
            entry.configure(state="normal" if key in allowed else "disabled")
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        try:
            argv = build_argv(self._current_options())
            self.preview_var.set(format_command(argv))
        except Exception as exc:
            self.preview_var.set(f"（参数无效）{exc}")

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    def _start(self) -> None:
        if self.runner.running:
            return
        try:
            argv = build_argv(self._current_options())
        except Exception as exc:
            messagebox.showerror("无法启动", str(exc))
            return
        self._stop_requested = False
        self._set_running(True)
        self.status_var.set("运行中…")
        self._append_log("─" * 60)
        self._append_log(f">>> 启动：{format_command(argv)}")
        self._append_log(f">>> 工作目录：{PROJECT_ROOT}")
        try:
            self.runner.start(argv, PROJECT_ROOT)
        except Exception as exc:
            self._set_running(False)
            self.status_var.set("启动失败")
            self._append_log(f"启动失败：{exc}")
            messagebox.showerror("启动失败", str(exc))

    def _stop(self) -> None:
        if not self.runner.running:
            return
        self._stop_requested = True
        self.status_var.set("正在停止…")
        self._append_log(">>> 用户请求停止，正在结束子进程")
        self.runner.stop()

    def _poll_events(self) -> None:
        while True:
            try:
                kind, payload = self.runner.events.get_nowait()
            except Empty:
                break
            if kind == "log":
                self._append_log(str(payload))
            elif kind == "done":
                code = int(payload) if payload is not None else -1
                self._set_running(False)
                if self._stop_requested:
                    self.status_var.set(f"已停止（退出码 {code}）")
                    self._append_log(f">>> 已停止，退出码 {code}")
                elif code == 0:
                    self.status_var.set("完成")
                    self._append_log(">>> 完成，退出码 0")
                else:
                    self.status_var.set(f"失败（退出码 {code}）")
                    self._append_log(f">>> 结束，退出码 {code}")
                self._stop_requested = False
        self.after(80, self._poll_events)

    def _open_dir(self, rel: str) -> None:
        try:
            open_directory(rel)
        except Exception as exc:
            messagebox.showerror("无法打开目录", str(exc))

    def confirm_close(self) -> bool:
        if not self.runner.running:
            return True
        if not messagebox.askyesno("退出", "任务仍在运行，确定结束子进程并退出？"):
            return False
        self.runner.stop()
        return True


class GrowthStockApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("成长股文档工具")
        self.geometry("920x720")
        self.minsize(780, 560)
        self.panel = GrowthStockPanel(self)
        self.panel.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self) -> None:
        if self.panel.confirm_close():
            self.destroy()


def run_app() -> None:
    _enable_dpi()
    app = GrowthStockApp()
    app.mainloop()
