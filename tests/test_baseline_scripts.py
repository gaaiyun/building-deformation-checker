from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SAMPLE_ROOT = Path("C:/Users/gaaiy/Desktop/建筑变形监测Agent")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sample_exists(relative: str) -> bool:
    candidates = [
        ROOT / relative,
        ROOT / "test_pdfs" / relative,
        LEGACY_SAMPLE_ROOT / relative,
        LEGACY_SAMPLE_ROOT / "test_pdfs" / relative,
    ]
    return any(path.exists() for path in candidates)


def test_run_tool_tests_cases_are_decoded_and_resolvable():
    mod = _load_module(ROOT / "baseline" / "run_tool_tests.py", "run_tool_tests_cases")

    expected = [
        "质安模板-错误版.pdf",
        "质安模板-正确版.pdf",
        "深工勘模板-错误版.pdf",
        "深工勘模板-正确版.pdf",
        "展誉模板-错误版.pdf",
        "展誉模板-正确版.pdf",
    ]

    actual = [case[0] for case in mod.TEST_CASES]
    assert actual == expected
    assert all(_sample_exists(name) for name in expected)
    assert mod._resolve_pdf_path("质安模板-错误版.pdf").exists()


def test_run_original_pdfs_cases_are_decoded_and_resolvable():
    mod = _load_module(ROOT / "baseline" / "run_original_pdfs.py", "run_original_pdf_cases")

    expected = [
        "【监测2023011-017】鱼珠乐天智能科技创新中心(1).pdf",
        "监测报告检查（测试）.pdf",
        "红土创新广场项目基坑监测报告第133期-hb.pdf",
        "恒大中心基坑支护工程地铁监测报告第209期（第3616次）.pdf",
        "设计的完整说明1.pdf",
    ]

    actual = [case[1] for case in mod.TEST_CASES]
    assert actual == expected
    assert all(_sample_exists(name) for name in expected)
    assert mod._resolve_pdf_path("监测报告检查（测试）.pdf").exists()
