"""成长股文档工具图形界面入口。

用法（工作目录任意，程序会把子进程 cwd 设为项目根）：
  python app_gui/main.py
  python main.py          # 在 app_gui 目录内
"""

from __future__ import annotations

import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from commands import GuiOptions, PROJECT_ROOT, build_argv, format_command  # noqa: E402
from window import GrowthStockApp, run_app  # noqa: E402


def self_test() -> int:
    opts = GuiOptions(command="download", codes="600900", limit="1")
    argv = build_argv(opts)
    print("ARGV:", argv)
    print("CMD:", format_command(argv))
    if argv[0] != sys.executable:
        raise SystemExit("python 路径不符")
    if Path(argv[1]) != PROJECT_ROOT / "main.py":
        raise SystemExit("main.py 路径不符")
    if argv[2:] != ["download", "--codes", "600900", "--limit", "1"]:
        raise SystemExit(f"参数拼装不符：{argv[2:]}")
    extra = [
        GuiOptions(command="screen"),
        GuiOptions(command="policies", industries="集成电路", per_industry_max="8"),
        GuiOptions(command="update-policies", industries="人工智能"),
        GuiOptions(command="finance", codes="000333", limit="1"),
        GuiOptions(command="run", limit="3"),
        GuiOptions(command="dianjin", codes="600900", limit="1"),
    ]
    for item in extra:
        print(item.command, "->", format_command(build_argv(item)))
    app = GrowthStockApp()
    app.update_idletasks()
    app.update()
    print(f"WINDOW_OK title={app.title()!r}")
    app.destroy()
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        return self_test()
    run_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
