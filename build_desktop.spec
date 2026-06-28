# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 配置 - 把桌面版打包为单文件 Windows .exe

用法：
    pyinstaller build_desktop.spec --clean --noconfirm

输出：
    dist/BuildingDeformationChecker.exe (~80–120 MB)

说明：
    - onefile 模式：单文件分发，启动时解压到临时目录（首次启动多 3-5 秒）
    - console=False：双击启动，无终端窗口
    - 排除 unused Qt 模块（QWebEngine 等）以缩小体积
    - 排除 streamlit / matplotlib 等不在桌面版用的库
"""

import sys
from pathlib import Path

block_cipher = None

# 项目根目录
PROJ_ROOT = Path(SPECPATH).resolve()


def _is_external_workspace_path(raw_path: str) -> bool:
    """Remove editable installs from sibling repos before PyInstaller analysis."""
    if not raw_path:
        return False
    try:
        path = Path(raw_path).resolve()
        path.relative_to(PROJ_ROOT)
        return False
    except ValueError:
        pass
    except OSError:
        return False

    try:
        path.relative_to(PROJ_ROOT.parent)
        return True
    except ValueError:
        return False


sys.path[:] = [path for path in sys.path if not _is_external_workspace_path(path)]

# ─── Analysis 阶段 ─────────────────────────────────────
a = Analysis(
    ['desktop.py'],
    pathex=[str(PROJ_ROOT)],
    binaries=[],
    datas=[
        # 把 src/ 目录全部纳入（避免 hidden import 漏掉子模块）
        ('src', 'src'),
        ('gui_desktop', 'gui_desktop'),
        ('assets', 'assets'),
        # 默认 .env.example 给用户参考（用户需自己创建 .env）
        ('.env.example', '.'),
    ],
    hiddenimports=[
        # PySide6 桌面依赖
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        # PDF 处理
        'pdfplumber',
        'pymupdf',
        'fitz',
        'pypdfium2',
        # docx 导出
        'docx',
        'docx.oxml',
        'docx.oxml.ns',
        # Excel 中间层导出
        'openpyxl',
        'openpyxl.styles',
        'openpyxl.utils',
        # 配置存储
        'keyring',
        'keyring.backends',
        'keyring.backends.Windows',
        # LLM 客户端
        'openai',
        # markdown 转 HTML
        'markdown',
        'markdown.extensions.tables',
        'markdown.extensions.fenced_code',
        # 工具
        'requests',
        # 项目内部模块（保险起见显式列出）
        'src.core.pipeline',
        'src.utils.text_normalize',
        'src.utils.dotenv_loader',
        'src.utils.llm_client',
        'src.tools.pdf_extractor',
        'src.tools.llm_parser',
        'src.tools.table_analyzer',
        'src.tools.calculation_checker',
        'src.tools.statistics_checker',
        'src.tools.logic_checker',
        'src.tools.self_verifier',
        'src.tools.report_generator',
        'src.tools.export_formats',
        'src.tools.extraction_quality',
        'src.models.data_models',
        'gui_desktop.main_window',
        'gui_desktop.worker',
        'gui_desktop.settings_store',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除桌面版不用的库，缩小体积
        'streamlit',
        'matplotlib',
        'tkinter',
        'IPython',
        'jupyter',
        'notebook',
        'pytest',
        'pandas',  # 项目本身不用 pandas
        'numpy',   # 不用
        'scipy',
        # 排除其它 Qt 子模块（默认会被发现）
        'PySide6.QtBluetooth',
        'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
        'PySide6.QtDesigner',
        'PySide6.QtHelp',
        'PySide6.QtLocation',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtNetworkAuth',
        'PySide6.QtNfc',
        'PySide6.QtOpenGL',
        'PySide6.QtOpenGLWidgets',
        'PySide6.QtPositioning',
        'PySide6.QtPrintSupport',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtQuick3D',
        'PySide6.QtQuickWidgets',
        'PySide6.QtRemoteObjects',
        'PySide6.QtSensors',
        'PySide6.QtSerialBus',
        'PySide6.QtSerialPort',
        'PySide6.QtSql',
        'PySide6.QtStateMachine',
        'PySide6.QtTest',
        'PySide6.QtTextToSpeech',
        'PySide6.QtUiTools',
        'PySide6.QtWebChannel',
        'PySide6.QtWebEngineCore',     # 重要！QtWebEngine 单独 200 MB
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebSockets',
        'PySide6.QtXml',
        'PySide6.Qt3DAnimation',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DExtras',
        'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic',
        'PySide6.Qt3DRender',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='BuildingDeformationChecker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                # UPX 压缩（如果系统有 upx 命令），可显著减小体积
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # 双击无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJ_ROOT / 'assets' / 'city_safety_iot.ico'),
)
