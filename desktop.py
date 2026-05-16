"""桌面版入口 - 启动 PySide6 GUI

用法：
    python desktop.py
"""

from __future__ import annotations

import sys

from gui_desktop.main_window import run_app


if __name__ == "__main__":
    sys.exit(run_app())
