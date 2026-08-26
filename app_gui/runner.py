"""在后台线程里跑子进程，把 stdout/stderr 逐行送进队列。"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from queue import Queue
from typing import IO


class ProcessRunner:
    """一次只跑一个子进程；停止时 terminate，超时再 kill。"""

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
            code = proc.wait()
            self.events.put(("done", code))
