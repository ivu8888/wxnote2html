#!/usr/bin/env python3
"""wxnote2html standalone entry point for PyInstaller packaging"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# 强制 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from wxnote2html.main import main

if __name__ == "__main__":
    main()
