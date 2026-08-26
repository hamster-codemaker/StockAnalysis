"""`python -m launcher` 入口：转发到套件 CLI（GUI / 日报 / 自选股 / 点金术）。"""

from launcher.suite import main

if __name__ == "__main__":
    raise SystemExit(main())
