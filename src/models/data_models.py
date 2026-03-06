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
    warning_value: Optional[float] = None   # 报警值
    control_value: Optional[float] = None   # 控制值
    rate_limit: Optional[float] = None      # 变化速率限值


@dataclass
class MeasurementPoint:
    """单个测点的一行数据"""
    point_id: str
    initial_value: Optional[float] = None       # 初始测值
    previous_value: Optional[float] = None      # 上次测值
    current_value: Optional[float] = None       # 本次测值
    current_change: Optional[float] = None      # 本次变化量（报告中给出的）
    cumulative_change: Optional[float] = None   # 累计变化量（报告中给出的）
    change_rate: Optional[float] = None         # 变化速率（报告中给出的）
    safety_status: str = ""                     # 安全状态（报告中给出的）


@dataclass
class DeepDisplacementPoint:
    """深层水平位移（按深度的测点）"""
    depth: float
    previous_cumulative: Optional[float] = None  # 上次累计量
    current_cumulative: Optional[float] = None   # 本次累计量
    change_rate: Optional[float] = None          # 变化速率


@dataclass
class StatisticsSummary:
    """表底的统计摘要"""
    positive_max_id: str = ""
    positive_max_value: Optional[float] = None
    negative_max_id: str = ""
    negative_max_value: Optional[float] = None
    max_rate_id: str = ""
    max_rate_value: Optional[float] = None
    # 锚索拉力等特殊统计
    max_force_id: str = ""
    max_force_value: Optional[float] = None
    min_force_id: str = ""
    min_force_value: Optional[float] = None


@dataclass
class MonitoringTable:
    """一张监测数据成果表"""
    monitoring_item: str = ""                   # 监测项名称
    category: MonitoringCategory = MonitoringCategory.OTHER
    monitor_date: str = ""                      # 监测日期
    monitor_count: str = ""                     # 监测次数
    point_count: int = 0                        # 监测点数量
    equipment_type: str = ""                    # 设备类型
    equipment_model: str = ""                   # 设备型号
    borehole_id: str = ""                       # 测孔编号（深层位移用）
    borehole_depth: Optional[float] = None      # 测孔深度

    points: list[MeasurementPoint] = field(default_factory=list)
    deep_points: list[DeepDisplacementPoint] = field(default_factory=list)
    statistics: StatisticsSummary = field(default_factory=StatisticsSummary)


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


@dataclass
class CheckIssue:
    """检查发现的问题"""
    severity: str           # "error" | "warning" | "info"
    table_name: str         # 所在表名
    point_id: str           # 测点编号
    field_name: str         # 字段名
    expected_value: str     # 期望值
    actual_value: str       # 实际值
    message: str            # 描述

    def __str__(self):
        icon = {"error": "[错误]", "warning": "[警告]", "info": "[提示]"}.get(
            self.severity, "[?]"
        )
        return (
            f"{icon} {self.table_name} - {self.point_id} - {self.field_name}: "
            f"{self.message} (期望: {self.expected_value}, 实际: {self.actual_value})"
        )
