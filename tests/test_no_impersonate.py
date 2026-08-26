"""生产模块不得伪造浏览器 TLS（impersonate）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCAN_DIRS = ("dianjin", "tech_analysis", "stock_screener", "launcher", "tools")
FORBIDDEN = ('impersonate="chrome', "impersonate='chrome", "impersonate=chrome")


class TestNoTlsImpersonation(unittest.TestCase):
    def test_source_has_no_impersonate_calls(self):
        hits: list[str] = []
        for folder in SCAN_DIRS:
            root = ROOT / folder
            if not root.is_dir():
                continue
            for path in root.rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8")
                for token in FORBIDDEN:
                    if token in text:
                        hits.append(f"{path.relative_to(ROOT)}: {token}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
