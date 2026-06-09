#!/usr/bin/env python3
"""
wxnote2html — 单文件运行入口
用法: python run.py -o note.html
"""

import sys

# 强制 UTF-8 输出，解决 Windows bash 中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))

from wxnote2html.main import main

if __name__ == "__main__":
    main()
