"""PyInstaller / 双击默认入口：统一 GUI；CLI 见 launcher.suite。"""

from __future__ import annotations

import os

os.environ.setdefault("MPLBACKEND", "Agg")

from launcher.suite import main

if __name__ == "__main__":
    raise SystemExit(main())
