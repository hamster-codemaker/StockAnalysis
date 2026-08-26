# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for StockAnalysis (onedir).

Rebuild: python pack.py
"""
from pathlib import Path
import sys

from PyInstaller.building.build_main import Analysis
from PyInstaller.building.api import PYZ, EXE, COLLECT
from PyInstaller.utils.hooks import collect_all, collect_data_files

SPECDIR = Path(SPECPATH)
SITE = Path(sys.executable).resolve().parent / "Lib" / "site-packages"
if not SITE.is_dir():
    import site as _site

    SITE = Path(_site.getsitepackages()[0])

datas = []
binaries = []
hiddenimports = []

for pkg in ("akshare", "curl_cffi", "py_mini_racer", "charset_normalizer", "pystray", "PIL"):
    pkg_datas, pkg_bins, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_bins
    hiddenimports += pkg_hidden

try:
    md, mb, mh = collect_all("matplotlib")
    datas += md
    binaries += mb
    hiddenimports += mh
except Exception:
    datas += collect_data_files("matplotlib")
    hiddenimports += ["matplotlib", "matplotlib.backends.backend_agg"]

for pyd in SITE.glob("*__mypyc*.pyd"):
    binaries.append((str(pyd), "."))

libs_dir = SITE / "curl_cffi.libs"
if libs_dir.is_dir():
    for dll in libs_dir.glob("*.dll"):
        binaries.append((str(dll), "curl_cffi.libs"))

tech_cfg = SPECDIR / "tech_analysis" / "config.yaml"
if tech_cfg.is_file():
    datas.append((str(tech_cfg), "tech_analysis"))

hiddenimports += [
    "lxml",
    "lxml.etree",
    "lxml.html",
    "lxml._elementpath",
    "bs4",
    "soupsieve",
    "yaml",
    "openpyxl",
    "xlrd",
    "html5lib",
    "jsonpath",
    "tabulate",
    "decorator",
    "cffi",
    "_cffi_backend",
    "certifi",
    "charset_normalizer",
    "idna",
    "urllib3",
    "requests",
    "pandas",
    "numpy",
    "curl_cffi.requests",
    "py_mini_racer",
    "matplotlib",
    "matplotlib.backends.backend_agg",
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.scrolledtext",
    "stock_screener",
    "stock_screener.config",
    "stock_screener.downloader",
    "stock_screener.screener",
    "stock_screener.finance",
    "stock_screener.datasources",
    "stock_screener.datasources.cninfo",
    "stock_screener.datasources.eastmoney",
    "stock_screener.datasources.govpolicy",
    "stock_screener.datasources.market",
    "tech_analysis",
    "tech_analysis.main",
    "tech_analysis.config",
    "tech_analysis.watchlist",
    "tech_analysis.market",
    "tech_analysis.network",
    "tech_analysis.indicators",
    "tech_analysis.signals",
    "tech_analysis.report",
    "tech_analysis.charts",
    "launcher",
    "launcher.suite",
    "launcher.gui",
    "launcher.paths",
    "launcher.settings",
    "launcher.tasks",
    "launcher.daily_report",
    "launcher.scheduler",
    "pystray",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "app_gui",
    "app_gui.window",
    "app_gui.commands",
    "app_gui.runner",
    "main",
    "dianjin",
    "dianjin.rules",
    "dianjin.em_cluster",
    "dianjin.em_clist",
    "dianjin.snapshot",
    "dianjin.kline",
    "dianjin.screen",
    "dianjin.enrich",
    "dianjin.report",
    "dianjin.pipeline",
    "dianjin.watchlist_mark",
    "dianjin.ths_yield",
]

seen_h = set()
hiddenimports = [h for h in hiddenimports if not (h in seen_h or seen_h.add(h))]

a = Analysis(
    [str(SPECDIR / "entry.py")],
    pathex=[str(SPECDIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "scipy",
        "IPython",
        "notebook",
        "pytest",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StockAnalysis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="StockAnalysis",
)
