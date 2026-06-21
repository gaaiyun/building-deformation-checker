from __future__ import annotations

from src.models.data_models import (
    MeasurementPoint,
    MonitoringCategory,
    MonitoringReport,
    MonitoringTable,
)
from src.tools.extraction_quality import analyze_extraction_quality


def test_sparse_initial_value_not_flagged_for_multi_period_change_table():
    """多期变化量表天然没有初始值列，不应仅因 initial_value 为空报提取异常。"""
    table = MonitoringTable(
        monitoring_item="基坑顶水平位移",
        category=MonitoringCategory.HORIZONTAL_DISP,
        point_count=2,
        points=[
            MeasurementPoint(point_id="WY1", current_change=0.3, cumulative_change=0.3, change_rate=0.3),
            MeasurementPoint(point_id="WY2", current_change=-0.2, cumulative_change=-0.2, change_rate=-0.2),
        ],
    )
    report = MonitoringReport(tables=[table])

    analyze_extraction_quality(report)
    flags = report.table_extraction_flags.get(0, [])

    assert not any("initial_value" in flag for flag in flags)


def test_sparse_current_change_is_flagged_when_change_table_loses_required_column():
    """已有累计/速率的变化量表如果本次变化缺失，应保留提取异常提示。"""
    table = MonitoringTable(
        monitoring_item="基坑顶水平位移",
        category=MonitoringCategory.HORIZONTAL_DISP,
        point_count=2,
        points=[
            MeasurementPoint(point_id="WY1", cumulative_change=0.3, change_rate=0.3),
            MeasurementPoint(point_id="WY2", cumulative_change=-0.2, change_rate=-0.2),
        ],
    )
    report = MonitoringReport(tables=[table])

    analyze_extraction_quality(report)
    flags = report.table_extraction_flags.get(0, [])

    assert any("current_change" in flag for flag in flags)


def test_cumulative_only_table_is_not_flagged_as_missing_absolute_columns():
    """只有累计值的长期监测表仍有有限核验价值，不应报 initial/current/current_change 缺失。"""
    table = MonitoringTable(
        monitoring_item="地铁隧道沉降累计变化",
        category=MonitoringCategory.SETTLEMENT,
        point_count=2,
        points=[
            MeasurementPoint(point_id="S1", cumulative_change=-1.2),
            MeasurementPoint(point_id="S2", cumulative_change=0.8),
        ],
    )
    report = MonitoringReport(tables=[table])

    analyze_extraction_quality(report)
    flags = report.table_extraction_flags.get(0, [])

    assert not any("initial_value" in flag for flag in flags)
    assert not any("current_value" in flag for flag in flags)
    assert not any("current_change" in flag for flag in flags)


def test_minor_row_count_gap_is_not_flagged_as_extraction_failure():
    """少量测点数差异可能来自停测/空行/统计行，不应默认制造噪声。"""
    table = MonitoringTable(
        monitoring_item="周边地面沉降",
        category=MonitoringCategory.SETTLEMENT,
        point_count=32,
        points=[
            MeasurementPoint(point_id=f"SM{i}", current_change=0.1, cumulative_change=0.2, change_rate=0.1)
            for i in range(27)
        ],
    )
    report = MonitoringReport(tables=[table])

    analyze_extraction_quality(report)
    flags = report.table_extraction_flags.get(0, [])

    assert not any("表头测点数" in flag for flag in flags)


def test_large_row_count_gap_is_flagged_as_extraction_risk():
    """行数差距明显时仍需提示可能漏解析。"""
    table = MonitoringTable(
        monitoring_item="周边地面沉降",
        category=MonitoringCategory.SETTLEMENT,
        point_count=32,
        points=[
            MeasurementPoint(point_id=f"SM{i}", current_change=0.1, cumulative_change=0.2, change_rate=0.1)
            for i in range(16)
        ],
    )
    report = MonitoringReport(tables=[table])

    analyze_extraction_quality(report)
    flags = report.table_extraction_flags.get(0, [])

    assert any("表头测点数" in flag for flag in flags)


def test_unmapped_numeric_source_field_is_flagged_and_counted():
    point = MeasurementPoint(
        point_id="MS7-3",
        current_value=4.6,
        current_change=0.0,
        cumulative_change=0.0,
        source_page=31,
        source_row_text="MS7-3 190.0 0.0 4.6 0.00 0.0 -- 400 480",
        source_field_map='{"current_value":4,"current_change":3}',
    )
    table = MonitoringTable(
        monitoring_item="锚索拉力",
        category=MonitoringCategory.ANCHOR_FORCE,
        point_count=1,
        points=[point],
    )
    report = MonitoringReport(tables=[table])

    analyze_extraction_quality(report)

    flags = report.table_extraction_flags[0]
    provenance = report.extraction_diagnostics["source_provenance"]
    assert any("1 个数值字段无法回溯原始列" in flag for flag in flags)
    assert provenance["numeric_field_count"] == 3
    assert provenance["mapped_numeric_field_count"] == 2
    assert provenance["unmapped_fields"] == {"cumulative_change": 1}
