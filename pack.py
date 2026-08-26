# -*- coding: utf-8 -*-
"""Build StockAnalysis.exe (onedir) then a single-file Inno Setup installer.

    python pack.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist" / "StockAnalysis"
SPEC = ROOT / "StockAnalysis.spec"
ISS = ROOT / "installer" / "StockAnalysis.iss"
INSTALLER_DIR = ROOT / "dist" / "installer"

USAGE = """成长股分析套件 —— 使用说明
========================================

本目录是独立发布包。请在本目录下双击或打开终端再运行 exe，
这样会读取本目录的 config.yaml 与 userdata/，数据写到本目录 data/。

启动
----
  双击 StockAnalysis.exe          打开统一图形界面（无黑色命令窗口）
  .\\StockAnalysis.exe --help     命令说明（此时会弹出控制台）
  .\\StockAnalysis.exe --daily-report   按开关生成详细日报（无窗口、无黑框）
  .\\StockAnalysis.exe --daemon         右下角托盘；到点自动出日报，不必点开图标

统一界面四个页：自选股 / 成长股工具 / 技术面 / 定时任务。
开机登录后若打开了「开机自启」，只出现托盘图标，不弹主窗口、不弹黑框。
需要时：左键或右键托盘 → 打开主界面。即使从不点开，到点仍会生成日报。
成长股 CLI、点金术、tech 子命令仍可用。

自选股（与机械选股、点金术独立）
--------------------------------
  userdata\\watchlist.txt
  每行一个 6 位代码，# 为注释。download / finance / policies / 技术面 / 日报自选股章节都读这一份。
  机械选股结果仍在 data\\screening\\，点金术在 data\\dianjin\\，不要把三份名单混在一起。
  点金术筛选不读自选股。命中且在自选股中的个股目录名为 代码_名称_自选，总表有「自选」列。
  自选股为空时，日报「自选股」文件夹写「今日无符合」，但仍会跑点金术全市场扫描。
  一键分析：界面「分析全部自选股」或 .\\StockAnalysis.exe --watchlist-analyze
  （技术面+基本面+综合分析，写入 data\\watchlist\\ 与当日日报集\\自选股\\，不跑点金术）。

点金术
------
  每日全 A 股（默认不含北交所、剔除 ST）：股息率>3%（同花顺股息率TTM；
  快照预填东财数据中心 DV_TTM，过 PE 关后再用 F10 分红表覆盖；
  不用 f183，不用 526792 振幅，不用腾讯年度口径），
  动态/静态/TTM 市盈率均 0<PE<20，收盘 < MA120 的 88%。extra 为加严子集：
  股息>4% 且 收盘 < MA120 的 82%，同一套 PE。
  生产路径：东财数据中心估值快照 + 腾讯动态市盈率（下标 52）→ 先过滤股息+PE
  → 只对幸存者拉前复权日线算 MA120（腾讯；无现成 MA120 接口）
  → 缺均线/财务时再补一轮 → 只对命中股做技术面/财务。
  个股图：最近 120 个交易日 K 线，叠加 MA8/MA24/MA120；BOLL(20,2)、MACD(12,26,9)、
  RSI 通达信 6/12/24。日线预热约 250 根。近 5 个交易日信号高亮。
  财务复用本地 data\\docs；从未下载时只拉公开财报接口 + 最新一期定期 PDF，不下全量历史文档。
  空名单写「今日无符合」，不中断日报。点金术与点金术extra 是并列文件夹。
  CLI：.\\StockAnalysis.exe dianjin
  --hist-limit / --limit / --screen-only 仅测试用，日报不会带这些参数。

政策索引
--------
  policies / update-policies / 界面「政策」在入库中央文件库后，
  只给「自选股」和「本地已有财报或研报」的股票重写 政策文件索引.md。
  不会仅为筛选 CSV 命中、但本地没有文档的股票建空目录。

开机自启与每日推送
------------------
  开关文件：userdata\\settings.yaml
    autostart: true/false      开机自启（任务名 StockAnalysisAutostart，--daemon）
    daily_update: true/false   每日更新（任务名 StockAnalysisDaily）
    daily_time: "16:00"
    report_dir: ""             空则默认 桌面\\日报集
  也可在 GUI「定时任务」页勾选：立刻写回 YAML，计划任务在后台同步。
  每次运行都重新读该文件。daily_update=false 时 --daily-report 只打日志、不写日报。

日报
----
  默认每天 16:00（A股 15:00 收盘后一小时）
  桌面\\日报集\\YYYYMMDD\\
    总览.md
    自选股\\  总表.md / 总表.csv / 技术面汇总.md / 基本面汇总.md
      个股\\<代码_名称>\\  综合分析.md
        技术面\\  分析报告.md / 技术分析.png / signals.csv
        基本面\\  财务分析.md / 五张图（含单季） / 财务数据.csv
    点金术技术信号汇总.md|.csv    点金术extra技术信号汇总.md|.csv
    点金术\\  总表.md / 点金术.md|.csv / 技术信号汇总.md|.csv / 个股\\<代码_名称[_自选]>\\
    点金术extra\\  总表.md / 点金术extra.md|.csv / 点金术extra技术信号汇总.md|.csv / 个股\\...（独立文件夹，可单独打开）
  自选股与点金术并列分开放。本地财报已是最新则直接出图。点金术始终全市场，不读自选股当股票池；
  命中的自选股只改目录名和总表标注。K 线 120 个交易日 + MA8/24/120 / BOLL / MACD / RSI。

成长股 CLI 示例
--------------
  .\\StockAnalysis.exe screen
  .\\StockAnalysis.exe download --watchlist userdata\\watchlist.txt
  .\\StockAnalysis.exe finance --watchlist userdata\\watchlist.txt
  .\\StockAnalysis.exe policies --industries 通用设备 --max 8
  .\\StockAnalysis.exe update-policies --industries 通用设备 --max 8
  .\\StockAnalysis.exe tech --watchlist userdata\\watchlist.txt
  .\\StockAnalysis.exe --watchlist-analyze
  .\\StockAnalysis.exe dianjin

如何重新打包
------------
  在项目根目录执行：python pack.py
  先生成 dist\\StockAnalysis\\（onedir），再生成 dist\\installer\\StockAnalysisSetup.exe。
"""

TEMPLATE_SETTINGS = """# 成长股工具 — 开机自启与每日推送开关（单一真相来源）
# 改本文件 或 在统一 GUI「定时任务」页勾选，效果相同。
# GUI 每次切换开关会立刻写回本文件；每次运行都重新读取本文件。
autostart: true
daily_update: true
daily_time: '16:00'
report_dir: ''
earnings_season_months:
- 1
- 2
- 3
- 4
- 7
- 8
- 10
"""

TEMPLATE_WATCHLIST = """# 用户自选股（单一真相来源）
# 格式：每行一个 6 位股票代码；# 开头为注释，空行忽略
# 也可写成「代码,名称」或「代码 名称」
# 与机械选股结果 data/screening/ 完全独立，互不覆盖
688308,欧科亿
301500,飞南资源
"""


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    print("copied", src.name, "->", dest)


def _assemble_dist() -> None:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    src_cfg = ROOT / "config.yaml"
    if src_cfg.exists():
        _copy(src_cfg, DIST_DIR / "config.yaml")
    ud = DIST_DIR / "userdata"
    ud.mkdir(parents=True, exist_ok=True)
    src_wl = ROOT / "userdata" / "watchlist.txt"
    if src_wl.exists():
        _copy(src_wl, ud / "watchlist.txt")
    else:
        (ud / "watchlist.txt").write_text(TEMPLATE_WATCHLIST, encoding="utf-8")
    (ud / "settings.yaml").write_text(TEMPLATE_SETTINGS, encoding="utf-8")
    print("wrote userdata/settings.yaml template")
    src_ud_readme = ROOT / "userdata" / "README.md"
    if src_ud_readme.exists():
        _copy(src_ud_readme, ud / "README.md")
    (DIST_DIR / "使用说明.txt").write_text(USAGE, encoding="utf-8")
    print("wrote 使用说明.txt")
    # 安装包不要带开发机的 data/docs PDF 与运行痕迹
    data_dir = DIST_DIR / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)
        print("removed dist data/ (not shipped)")
    for junk_name in ("last_report.json", "daemon.log", "source_preference.yaml"):
        junk = ud / junk_name
        if junk.exists():
            junk.unlink()


def _cleanup_build_junk() -> None:
    build_dir = ROOT / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)
        print("removed build/")
    for junk in ROOT.glob("warn-*.txt"):
        junk.unlink(missing_ok=True)
    for junk in ROOT.glob("*.toc"):
        junk.unlink(missing_ok=True)


def find_iscc() -> Path | None:
    which = shutil.which("iscc") or shutil.which("ISCC")
    if which:
        return Path(which)
    for candidate in (
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        Path.home() / "AppData" / "Local" / "Programs" / "Inno Setup 6" / "ISCC.exe",
    ):
        if candidate.is_file():
            return candidate
    return None


def ensure_inno() -> Path:
    found = find_iscc()
    if found:
        return found
    print("未检测到 Inno Setup，尝试 winget 安装 JRSoftware.InnoSetup")
    subprocess.check_call(
        [
            "winget",
            "install",
            "-e",
            "--id",
            "JRSoftware.InnoSetup",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--disable-interactivity",
        ]
    )
    found = find_iscc()
    if not found:
        raise FileNotFoundError("已尝试安装 Inno Setup，仍找不到 ISCC.exe")
    return found


def build_installer() -> Path:
    if not ISS.is_file():
        raise FileNotFoundError(f"缺少 {ISS}")
    exe = DIST_DIR / "StockAnalysis.exe"
    internal = DIST_DIR / "_internal"
    if not exe.is_file() or not internal.is_dir():
        raise FileNotFoundError("请先完成 PyInstaller onedir（缺少 dist/StockAnalysis/StockAnalysis.exe 或 _internal）")
    iscc = ensure_inno()
    INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [str(iscc), str(ISS)]
    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))
    setup = INSTALLER_DIR / "StockAnalysisSetup.exe"
    if not setup.is_file() or setup.stat().st_size < 1024:
        raise RuntimeError(f"安装包未生成或为空：{setup}")
    print("installer ->", setup, f"({setup.stat().st_size / 1e6:.1f} MB)")
    return setup


def main() -> int:
    if not SPEC.exists():
        print("缺少 StockAnalysis.spec", file=sys.stderr)
        return 1
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)]
    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))
    _assemble_dist()
    _cleanup_build_junk()
    print("onedir ->", DIST_DIR / "StockAnalysis.exe")
    try:
        build_installer()
    except Exception as exc:
        print(f"安装包失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
