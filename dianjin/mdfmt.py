"""点金术报告共用的 Markdown 表格与数字格式化。

此前 report.py / signal_summary.py / tech_summary.py 各有一份同样实现，
集中到这里，三处引用同一份，避免改一处漏两处。
"""

from __future__ import annotations

from typing import Any

from dianjin.rules import to_float


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    """生成 Markdown 表格；空行集合渲染为一行「—」占位，避免出现空表。"""
    head = "| " + " | ".join(headers) + " |"
    sep = "|" + " --- |" * len(headers)
    if not rows:
        rows = [["—"] * len(headers)]
    body = ["| " + " | ".join("" if v is None else str(v) for v in row) + " |" for row in rows]
    return "\n".join([head, sep, *body])


def fmt_num(value: Any, digits: int = 2) -> str:
    """数字保留 digits 位小数；None/NaN/非数返回「—」（不会输出 "nan" 字样）。"""
    number = to_float(value)
    if number is None:
        return "—"
    return f"{number:.{digits}f}"
