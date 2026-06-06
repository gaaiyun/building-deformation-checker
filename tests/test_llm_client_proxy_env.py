from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models.data_models import (
    MeasurementPoint,
    MonitoringCategory,
    MonitoringReport,
    MonitoringTable,
    TableVerificationConfig,
    ThresholdConfig,
)
from src.tools.logic_checker import run_logic_checks
from src.utils.llm_client import create_openai_client, normalize_openai_base_url


def test_openai_client_ignores_invalid_no_proxy_ipv6_entries(monkeypatch):
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost,::1,127.0.0.0/8,::1/128")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1,127.0.0.0/8,::1/128")

    client = create_openai_client(api_key="sk-test", base_url="https://api.deepseek.com")
    try:
        assert str(client.base_url).startswith("https://api.deepseek.com")
    finally:
        client.close()


def test_base_url_normalization_accepts_bare_host_and_rejects_bad_port():
    assert normalize_openai_base_url("api.deepseek.com/") == "https://api.deepseek.com"

    try:
        normalize_openai_base_url("https://api.deepseek.com::1")
    except ValueError as exc:
        assert "LLM Base URL 无效" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("bad port should be rejected")


def test_logic_checker_falls_back_when_semantic_llm_client_cannot_start(monkeypatch):
    import src.config as cfg

    monkeypatch.setattr(cfg, "LLM_BASE_URL", "https://api.deepseek.com::1")
    report = MonitoringReport(
        thresholds=[
            ThresholdConfig(item_name="管线沉降", warning_value=10.0, rate_limit=1.0),
        ],
        tables=[
            MonitoringTable(
                monitoring_item="管线沉降",
                category=MonitoringCategory.SETTLEMENT,
                verification_config=TableVerificationConfig(unit="mm"),
                points=[
                    MeasurementPoint(
                        point_id="G1",
                        initial_value=0.0,
                        current_value=9.0,
                        current_change=1.0,
                        cumulative_change=9.0,
                        change_rate=0.9,
                        safety_status="正常",
                    )
                ],
            )
        ],
    )

    issues = run_logic_checks(report)

    assert isinstance(issues, list)
    assert report.threshold_map
