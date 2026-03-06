"""数据模型定义"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SafetyStatus(Enum):
    NORMAL = "正常"
    WARNING = "报警"
    ALARM = "控制"
    UNKNOWN = "未知"


class MonitoringCategory(Enum):
    """监测项大类"""
    HORIZONTAL_DISP = "水平位移"
    VERTICAL_DISP = "竖向位移"
    SETTLEMENT = "沉降"
    WATER_LEVEL = "水位"
    ANCHOR_FORCE = "锚索拉力"
    STRUT_FORCE = "支撑轴力"
    DEEP_HORIZONTAL = "深层水平位移"
    PILE_INCLINE = "测斜"
    CRACK = "裂缝"
    OTHER = "其他"


@dataclass
class ThresholdConfig:
    """报警/控制值配置"""
    item_name: str
    warning_value: Optional[float] = None
    control_value: Optional[float] = None
    rate_limit: Optional[float] = None


@dataclass
class TableVerificationConfig:
    """
    Per-table verification parameters, determined dynamically by LLM analysis.
    Replaces hardcoded category branches in checkers.
    """
    unit: str = "mm"
    unit_conversion: float = 1.0
    cumulative_tolerance: float = 0.15
    rate_tolerance: float = 0.05
    interval_days: Optional[float] = None
    direction_convention: str = ""
    initial_value_reliable: bool = True
    severity_for_cumulative: str = "error"


@dataclass
class MeasurementPoint:
    """单个测点的一行数据"""
    point_id: str
    initial_value: Optional[float] = None
    previous_value: Optional[float] = None
    current_value: Optional[float] = None
    current_change: Optional[float] = None
    cumulative_change: Optional[float] = None
    change_rate: Optional[float] = None
    safety_status: str = ""


@dataclass
class DeepDisplacementPoint:
    """深层水平位移（按深度的测点）"""
    depth: float
    previous_cumulative: Optional[float] = None
    current_cumulative: Optional[float] = None
    change_rate: Optional[float] = None


@dataclass
class StatisticsSummary:
    """表底的统计摘要"""
    positive_max_id: str = ""
    positive_max_value: Optional[float] = None
    negative_max_id: str = ""
    negative_max_value: Optional[float] = None
    max_rate_id: str = ""
    max_rate_value: Optional[float] = None
    max_force_id: str = ""
    max_force_value: Optional[float] = None
    min_force_id: str = ""
    min_force_value: Optional[float] = None


@dataclass
class MonitoringTable:
    """一张监测数据成果表"""
    monitoring_item: str = ""
    category: MonitoringCategory = MonitoringCategory.OTHER
    monitor_date: str = ""
    monitor_count: str = ""
    point_count: int = 0
    equipment_type: str = ""
    equipment_model: str = ""
    borehole_id: str = ""
    borehole_depth: Optional[float] = None

    points: list[MeasurementPoint] = field(default_factory=list)
    deep_points: list[DeepDisplacementPoint] = field(default_factory=list)
    statistics: StatisticsSummary = field(default_factory=StatisticsSummary)
    verification_config: TableVerificationConfig = field(
        default_factory=TableVerificationConfig
    )


@dataclass
class ReportSummaryItem:
    """简报汇总表中的一行"""
    monitoring_item: str = ""
    negative_max: str = ""
    negative_max_id: str = ""
    positive_max: str = ""
    positive_max_id: str = ""
    max_rate: str = ""
    max_rate_id: str = ""
    safety_status: str = ""


@dataclass
class MonitoringReport:
    """完整的监测报告数据"""
    project_name: str = ""
    monitoring_company: str = ""
    report_number: str = ""
    monitoring_period: str = ""
    monitoring_date: str = ""

    thresholds: list[ThresholdConfig] = field(default_factory=list)
    summary_items: list[ReportSummaryItem] = field(default_factory=list)
    tables: list[MonitoringTable] = field(default_factory=list)
    conclusion: str = ""
    raw_text: str = ""

    threshold_map: dict = field(default_factory=dict)
    summary_map: dict = field(default_factory=dict)


@dataclass
class CheckIssue:
    """检查发现的问题"""
    severity: str
    table_name: str
    point_id: str
    field_name: str
    expected_value: str
    actual_value: str
    message: str

    def __str__(self):
        icon = {"error": "[错误]", "warning": "[警告]", "info": "[提示]"}.get(
            self.severity, "[?]"
        )
        return (
            f"{icon} {self.table_name} - {self.point_id} - {self.field_name}: "
            f"{self.message} (期望: {self.expected_value}, 实际: {self.actual_value})"
        )
