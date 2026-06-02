"""main.py CLI 入口应复用统一 pipeline，而不是维护第二套编排逻辑。"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


def _main_ast() -> ast.Module:
    return ast.parse(MAIN_PATH.read_text(encoding="utf-8"))


def test_main_delegates_to_core_pipeline() -> None:
    tree = _main_ast()
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "RuntimeConfig" in imported
    assert "run_pipeline" in imported
    assert "run_pipeline" in called


def test_main_no_longer_imports_individual_tool_steps() -> None:
    tree = _main_ast()
    forbidden_modules = {
        "src.tools.pdf_extractor",
        "src.tools.llm_parser",
        "src.tools.extraction_quality",
        "src.tools.table_analyzer",
        "src.tools.calculation_checker",
        "src.tools.statistics_checker",
        "src.tools.logic_checker",
        "src.tools.self_verifier",
        "src.tools.report_generator",
    }
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert not (imported_modules & forbidden_modules)
