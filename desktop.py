"""桌面版入口 - 启动 PySide6 GUI

用法：
    python desktop.py

启动时会自动：
    1. 从项目根目录的 .env 加载环境变量（仅当 keyring 未配置 key 时回退）
    2. 把日志写到 stderr
    3. 启动 Qt 主事件循环
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path


def _configure_logging() -> None:
    log_dir = Path(os.environ.get("APPDATA") or Path.home()) / "BuildingDeformationChecker" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"desktop_{datetime.now():%Y%m%d}.log"

    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    def _excepthook(exc_type, exc, tb):
        logging.getLogger(__name__).exception("未捕获异常导致程序退出", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _excepthook


def main() -> int:
    _configure_logging()
    # 必须在 import gui_desktop 之前加载 .env，避免子模块导入时读不到 env
    from src.utils.dotenv_loader import load_dotenv
    load_dotenv()

    from gui_desktop.main_window import run_app
    return run_app()


if __name__ == "__main__":
    sys.exit(main())
