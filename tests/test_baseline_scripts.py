from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SAMPLE_ROOTS = [
    Path(p)
    for p in os.environ.get("BDC_SAMPLE_ROOTS", "").split(os.pathsep)
    if p.strip()
]


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
    ]
    for legacy_root in LEGACY_SAMPLE_ROOTS:
        candidates.extend([
            legacy_root / relative,
            legacy_root / "test_pdfs" / relative,
        ])
    return any(path.exists() for path in candidates)


def _missing_samples(names: list[str]) -> list[str]:
    return [name for name in names if not _sample_exists(name)]


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

    missing = _missing_samples(expected)
    if missing:
        pytest.skip("baseline sample PDFs are not present in this environment: " + ", ".join(missing))

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

    missing = _missing_samples(expected)
    if missing:
        pytest.skip("original sample PDFs are not present in this environment: " + ", ".join(missing))

    assert mod._resolve_pdf_path("监测报告检查（测试）.pdf").exists()


def test_score_recall_handles_empty_and_category_field_names():
    mod = _load_module(ROOT / "baseline" / "score_recall.py", "score_recall")

    assert mod._gt_keywords("") == []
    assert "沉降" in mod._gt_keywords("累计沉降 / (mm)")
    assert "水位" in mod._gt_keywords("地下水位 / (mm)")
