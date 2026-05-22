"""桌面版入口 - 启动 PySide6 GUI

用法：
    python desktop.py

启动时会自动：
    1. 从项目根目录的 .env 加载环境变量（仅当 keyring 未配置 key 时回退）
    2. 把日志写到 stderr
    3. 启动 Qt 主事件循环
"""

from __future__ import annotations

import sys


def main() -> int:
    # 必须在 import gui_desktop 之前加载 .env，避免子模块导入时读不到 env
    from src.utils.dotenv_loader import load_dotenv
    load_dotenv()

    from gui_desktop.main_window import run_app
    return run_app()


if __name__ == "__main__":
    sys.exit(main())
