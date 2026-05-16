"""轻量 .env 文件加载器（零外部依赖）

为什么不用 python-dotenv：
    - 项目目标依赖最小化，避免 PyInstaller 打包额外引入
    - .env 格式简单（KEY=VALUE 一行一项），手写 50 行解析比加依赖更直观

行为约定：
    - 文件不存在不报错（静默跳过，保持原有 os.environ 不变）
    - 同名 key 已存在于 os.environ 时**不覆盖**（环境变量优先）
    - 支持 ``KEY=value`` / ``KEY="value with spaces"`` / 行内 # 注释
    - 支持中文 UTF-8
    - 空行与以 ``#`` 开头的行跳过

典型用法（在入口脚本最开头调用）::

    from src.utils.dotenv_loader import load_dotenv
    load_dotenv()  # 读取项目根目录的 .env
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _strip_quotes(value: str) -> str:
    """去掉首尾配对的引号（单或双）"""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def _parse_line(line: str) -> Optional[tuple[str, str]]:
    """解析一行 .env 内容；返回 (key, value) 或 None。

    支持：
        ``KEY=value``
        ``KEY="value with spaces"``
        ``KEY=value  # inline comment``
        ``# whole line comment``
        ``  ``（空行）
    """
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    if "=" not in s:
        return None

    key, _, raw_value = s.partition("=")
    key = key.strip()
    if not key or not key.replace("_", "").isalnum():
        # 非法 key 名（含特殊字符）
        return None

    value = raw_value.strip()
    # 去除引号包裹（引号内可以有 #）
    if (len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'")):
        value = _strip_quotes(value)
    else:
        # 没引号的话，行内 # 视为注释开始
        if "#" in value:
            value = value.split("#", 1)[0].rstrip()

    return key, value


def load_dotenv(path: Optional[Path | str] = None, override: bool = False) -> int:
    """加载 .env 文件到 os.environ。

    Args:
        path: .env 文件路径；None 则在项目根目录寻找。
        override: 若为 True，文件中的值会覆盖已有的 os.environ；
                 默认 False，保持 "环境变量优先" 的常规约定。

    Returns:
        成功加载的键值对数量；文件不存在返回 0。
    """
    if path is None:
        # 项目根目录 = src/utils 的祖父目录
        root = Path(__file__).resolve().parents[2]
        path = root / ".env"
    else:
        path = Path(path)

    if not path.exists():
        return 0

    loaded = 0
    try:
        with path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                parsed = _parse_line(line)
                if parsed is None:
                    continue
                key, value = parsed
                if not override and key in os.environ:
                    continue
                os.environ[key] = value
                loaded += 1
    except OSError as exc:
        logger.warning("加载 .env 失败 (%s): %s", path, exc)
        return loaded
    except UnicodeDecodeError as exc:
        logger.warning(".env 文件编码错误，请保存为 UTF-8: %s", exc)
        return loaded

    logger.info("已加载 .env（%d 个变量）来自 %s", loaded, path)
    return loaded


__all__ = ["load_dotenv"]
