"""技术面分析独立图形界面。

用法：
  python tech_analysis/gui.py
  python gui.py          # 在 tech_analysis 目录内

通过子进程调用本目录 main.py，不导入指标/行情等业务模块。
与成长股文档工具（app_gui）是两套窗口，不要混用。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import IO

TECH_DIR = Path(__file__).resolve().parent
MAIN_PY = TECH_DIR / "main.py"
_PROJECT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else TECH_DIR.parent
_USER_WL = _PROJECT / "userdata" / "watchlist.txt"
DEFAULT_WATCHLIST = _USER_WL if _USER_WL.is_file() else TECH_DIR / "watchlist.txt"
DEFAULT_CONFIG = TECH_DIR / "config.yaml"

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


def format_command(argv: list[str]) -> str:
    parts: list[str] = []
    for item in argv:
        if any(ch.isspace() for ch in item) or not item:
            parts.append(f'"{item}"')
        else:
            parts.append(item)
    return " ".join(parts)


def build_argv(
    watchlist: str = "",
    config: str = "",
    verbose: bool = False,
    python: str | None = None,
) -> list[str]:
    if not MAIN_PY.is_file():
        raise FileNotFoundError("技术面分析模块尚未就绪：找不到 tech_analysis/main.py")
    argv = [python or sys.executable, str(MAIN_PY)]
    if verbose:
        argv.append("-v")
    config = (config or "").strip()
    if config:
        argv.extend(["--config", config])
    watchlist = (watchlist or "").strip()
    if watchlist:
        argv.extend(["--watchlist", watchlist])
    return argv


def resolve_output_dir(config_path: str) -> Path:
    rel = "output"
    path = Path(config_path) if config_path.strip() else DEFAULT_CONFIG
    if not path.is_absolute():
        path = TECH_DIR / path
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rel = str((data.get("paths") or {}).get("output_dir") or "output")
    except Exception:
        pass
    out = Path(rel)
    return out if out.is_absolute() else TECH_DIR / out


class ProcessRunner:
    def __init__(self) -> None:
        self.events: Queue[tuple[str, object]] = Queue()
        self._proc: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def start(self, argv: list[str], cwd: Path) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError("已有任务在运行")
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            flags = 0
            if sys.platform == "win32":
                flags = subprocess.CREATE_NEW_PROCESS_GROUP
                flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=flags,
            )
            self._thread = threading.Thread(target=self._pump, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _pump(self) -> None:
        proc = self._proc
        if proc is None:
            self.events.put(("done", -1))
            return
        stream: IO[str] | None = proc.stdout
        try:
            if stream is not None:
                for line in stream:
                    self.events.put(("log", line.rstrip("\r\n")))
        finally:
            self.events.put(("done", proc.wait()))


class TechAnalysisApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("技术面分析")
        self.geometry("880x700")
        self.minsize(720, 540)

        self.runner = ProcessRunner()
        self._stop_requested = False
        self._saved_text = ""

        self.watchlist_var = tk.StringVar(value=str(DEFAULT_WATCHLIST))
        self.config_var = tk.StringVar(value=str(DEFAULT_CONFIG))
        self.verbose_var = tk.BooleanVar(value=False)
        self.preview_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就绪")

        self._build()
        for var in (self.watchlist_var, self.config_var, self.verbose_var):
            var.trace_add("write", lambda *_: self._refresh_preview())
        self._load_watchlist(show_error=False)
        self._refresh_preview()
        self.after(80, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        self.option_add("*Font", FONT_UI)
        pad = {"padx": 10, "pady": 6}

        header = ttk.Frame(self)
        header.pack(fill="x", **pad)
        ttk.Label(header, text="自选股技术面分析", font=FONT_UI_BOLD).pack(anchor="w")
        ttk.Label(
            header,
            text="独立工具：子进程调用 tech_analysis/main.py，不导入分析代码，也不打开成长股文档窗口。",
            foreground="#444",
        ).pack(anchor="w")

        files = ttk.LabelFrame(self, text="文件")
        files.pack(fill="x", padx=10, pady=(0, 6))
        files.columnconfigure(1, weight=1)
        self._file_row(files, 0, "自选股文件", self.watchlist_var, self._browse_watchlist, self._reload_watchlist)
        self._file_row(files, 1, "配置文件", self.config_var, self._browse_config, None)
        extra = ttk.Frame(files)
        extra.grid(row=2, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 8))
        ttk.Checkbutton(extra, text="详细日志（-v）", variable=self.verbose_var).pack(side="left")

        editor_box = ttk.LabelFrame(self, text="自选股内容（可编辑，运行前自动保存）")
        editor_box.pack(fill="both", expand=False, padx=10, pady=(0, 6))
        bar = ttk.Frame(editor_box)
        bar.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Button(bar, text="保存自选股", command=self._save_watchlist).pack(side="right")
        self.editor = ScrolledText(editor_box, height=8, wrap="word", font=FONT_LOG)
        self.editor.pack(fill="both", expand=True, padx=8, pady=8)

        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=10, pady=(0, 6))
        self.start_btn = ttk.Button(actions, text="开始分析", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(actions, text="停止", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="打开输出目录", command=self._open_output).pack(side="left", padx=(8, 0))
        ttk.Label(actions, textvariable=self.status_var, foreground="#1a5f2a").pack(
            side="left", padx=16
        )

        preview = ttk.LabelFrame(self, text="将执行的命令")
        preview.pack(fill="x", padx=10, pady=(0, 6))
        ttk.Label(
            preview, textvariable=self.preview_var, wraplength=820, foreground="#222"
        ).pack(fill="x", padx=8, pady=6)

        log_box = ttk.LabelFrame(self, text="运行日志")
        log_box.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        log_bar = ttk.Frame(log_box)
        log_bar.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Button(log_bar, text="清空日志", command=self._clear_log).pack(side="right")
        self.log = ScrolledText(
            log_box, height=14, wrap="word", font=FONT_LOG, state="disabled"
        )
        self.log.pack(fill="both", expand=True, padx=8, pady=8)

    def _file_row(self, parent, row, label, variable, browse, extra_cmd) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="e", padx=(8, 6), pady=4)
        mid = ttk.Frame(parent)
        mid.grid(row=row, column=1, sticky="ew", pady=4)
        mid.columnconfigure(0, weight=1)
        ttk.Entry(mid, textvariable=variable).grid(row=0, column=0, sticky="ew")
        ttk.Button(mid, text="浏览…", command=browse).grid(row=0, column=1, padx=(6, 0))
        if extra_cmd is not None:
            ttk.Button(mid, text="重新加载", command=extra_cmd).grid(row=0, column=2, padx=(6, 0))

    def _browse_watchlist(self) -> None:
        path = filedialog.askopenfilename(
            title="选择自选股文件",
            initialdir=str(TECH_DIR),
            filetypes=[
                ("文本 / CSV", "*.txt;*.csv"),
                ("文本", "*.txt"),
                ("CSV", "*.csv"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self.watchlist_var.set(path)
            self._load_watchlist()

    def _browse_config(self) -> None:
        path = filedialog.askopenfilename(
            title="选择配置文件",
            initialdir=str(TECH_DIR),
            filetypes=[("YAML", "*.yaml;*.yml"), ("所有文件", "*.*")],
        )
        if path:
            self.config_var.set(path)

    def _reload_watchlist(self) -> None:
        self._load_watchlist()

    def _watchlist_path(self) -> Path:
        raw = self.watchlist_var.get().strip() or str(DEFAULT_WATCHLIST)
        path = Path(raw)
        return path if path.is_absolute() else TECH_DIR / path

    def _load_watchlist(self, show_error: bool = True) -> None:
        path = self._watchlist_path()
        if not path.is_file():
            if show_error:
                messagebox.showerror("无法加载", f"找不到自选股文件：{path}")
            return
        text = path.read_text(encoding="utf-8-sig")
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", text)
        self._saved_text = text
        self.watchlist_var.set(str(path))

    def _editor_text(self) -> str:
        return self.editor.get("1.0", "end-1c")

    def _save_watchlist(self, quiet: bool = False) -> bool:
        path = self._watchlist_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            text = self._editor_text()
            path.write_text(text, encoding="utf-8")
            self._saved_text = text
            self.watchlist_var.set(str(path))
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return False
        if not quiet:
            self.status_var.set(f"已保存 {path.name}")
        return True

    def _refresh_preview(self) -> None:
        try:
            argv = build_argv(
                watchlist=self.watchlist_var.get(),
                config=self.config_var.get(),
                verbose=bool(self.verbose_var.get()),
            )
            self.preview_var.set(format_command(argv))
        except Exception as exc:
            self.preview_var.set(f"（无法生成命令）{exc}")

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
        if not MAIN_PY.is_file():
            messagebox.showinfo("模块尚未就绪", "找不到 tech_analysis/main.py，请稍后再试。")
            return
        if self._editor_text() != self._saved_text:
            if not self._save_watchlist(quiet=True):
                return
            self._append_log(f">>> 已保存自选股：{self._watchlist_path()}")
        try:
            argv = build_argv(
                watchlist=str(self._watchlist_path()),
                config=self.config_var.get(),
                verbose=bool(self.verbose_var.get()),
            )
        except Exception as exc:
            messagebox.showerror("无法启动", str(exc))
            return
        self._stop_requested = False
        self._set_running(True)
        self.status_var.set("运行中…")
        self._append_log("─" * 60)
        self._append_log(f">>> 启动：{format_command(argv)}")
        self._append_log(f">>> 工作目录：{TECH_DIR}")
        try:
            self.runner.start(argv, TECH_DIR)
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

    def _open_output(self) -> None:
        try:
            out = resolve_output_dir(self.config_var.get())
            out.mkdir(parents=True, exist_ok=True)
            os.startfile(str(out))
        except Exception as exc:
            messagebox.showerror("无法打开目录", str(exc))

    def _on_close(self) -> None:
        if self.runner.running:
            if not messagebox.askyesno("退出", "任务仍在运行，确定结束子进程并退出？"):
                return
            self.runner.stop()
        self.destroy()


def self_test() -> int:
    argv = build_argv(
        watchlist=str(DEFAULT_WATCHLIST),
        config=str(DEFAULT_CONFIG),
        verbose=False,
    )
    print("ARGV:", argv)
    print("CMD:", format_command(argv))
    assert argv[0] == sys.executable
    assert Path(argv[1]) == MAIN_PY
    assert argv[2:] == ["--config", str(DEFAULT_CONFIG), "--watchlist", str(DEFAULT_WATCHLIST)]
    app = TechAnalysisApp()
    app.update_idletasks()
    app.update()
    title = app.title()
    app.destroy()
    print(f"WINDOW_OK title={title!r}")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        return self_test()
    _enable_dpi()
    TechAnalysisApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
