# 跨期连续性核查 · 设计草案

## 触发场景

监测公司模板（如展誉）使用**横向多期布局**，且**无独立初始值列**：

```
| 测点 | 第N次本次 | 第N次累计 | 第N次速率 | 第N+1次本次 | 第N+1次累计 | 第N+1次速率 |
| LZ1  | 0.02     | 0.03     | 0.02     | 0.52       | 0.55       | 0.52       |
```

现有 `(current - initial) × conv = cumulative` 公式不适用，因为没有 initial 列。

## 设计：跨期连续性验证

公式：`累计_{N+1} = 累计_N + 本次_{N+1}`

实现位置：`src/tools/calculation_checker.py`

```python
def check_cross_period_continuity(
    report: MonitoringReport,
    issues: list[CheckIssue],
) -> None:
    """同测点跨期：累计_{N+1} = 累计_N + 本次_{N+1}

    适用横向多期布局或拆分成多张表的多期场景。
    LLM 把同一 monitoring_item 的不同期拆成不同 table 后，按 monitor_date
    排序，对每个测点做跨期累计连续性检查。
    """
    from collections import defaultdict

    # 按 monitoring_item + borehole_id 分组
    groups: dict[tuple[str, str], list[MonitoringTable]] = defaultdict(list)
    for t in report.tables:
        if not t.points:
            continue  # 跳过深层位移（已有独立校验）
        groups[(t.monitoring_item, t.borehole_id or "")].append(t)

    for (item, bh), tables in groups.items():
        if len(tables) < 2:
            continue
        # 按 monitor_date 排序（缺失日期的排最后）
        sorted_tbls = sorted(tables, key=lambda t: (t.monitor_date or "9999", t.monitor_count or ""))

        for n, n1 in zip(sorted_tbls, sorted_tbls[1:]):
            # 收集 n 期各测点的累计
            n_cums = {p.point_id: p.cumulative_change for p in n.points if p.cumulative_change is not None}

            for pt in n1.points:
                if pt.cumulative_change is None or pt.current_change is None:
                    continue
                if pt.point_id not in n_cums:
                    continue
                expected = n_cums[pt.point_id] + pt.current_change
                tol = max(0.15, abs(pt.cumulative_change) * 0.05)
                if abs(expected - pt.cumulative_change) > tol:
                    issues.append(CheckIssue(
                        severity="error",
                        table_name=item,
                        point_id=pt.point_id,
                        field_name="跨期累计连续性",
                        expected_value=f"{expected:.3f}",
                        actual_value=f"{pt.cumulative_change:.3f}",
                        message=(
                            f"{n.monitor_date or n.monitor_count} 累计={n_cums[pt.point_id]:.2f} + "
                            f"{n1.monitor_date or n1.monitor_count} 本次={pt.current_change:.2f} "
                            f"= {expected:.2f}，但 {n1.monitor_date or n1.monitor_count} 累计报告值 = {pt.cumulative_change:.2f}"
                        ),
                    ))
```

## 注册到 run_calculation_checks

```python
def run_calculation_checks(report: MonitoringReport) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    for table in report.tables:
        check_cumulative_change(table, issues)
        check_change_rate(table, issues)
        ...
    # 新增：跨表跨期检查（不在单表循环里）
    check_cross_period_continuity(report, issues)
    return issues
```

## 单元测试 (tests/test_cross_period_continuity.py)

```python
def test_continuity_detects_cumulative_break():
    """期N 累计=1.0, 期N+1 本次=0.5 → 期N+1 累计应为 1.5; 若报告 3.5 应报错"""
    t1 = MonitoringTable(
        monitoring_item="立柱沉降", monitor_date="2026-05-11",
        points=[MeasurementPoint(point_id="LZ1", cumulative_change=1.0, current_change=0.0)],
    )
    t2 = MonitoringTable(
        monitoring_item="立柱沉降", monitor_date="2026-05-12",
        points=[MeasurementPoint(point_id="LZ1", cumulative_change=3.5, current_change=0.5)],
    )
    report = MonitoringReport(tables=[t1, t2])
    issues = run_calculation_checks(report)
    cross_issues = [i for i in issues if i.field_name == "跨期累计连续性"]
    assert len(cross_issues) == 1
    assert "LZ1" in cross_issues[0].point_id

def test_continuity_passes_when_consistent():
    t1 = MonitoringTable(
        monitoring_item="立柱沉降", monitor_date="2026-05-11",
        points=[MeasurementPoint(point_id="LZ1", cumulative_change=1.0, current_change=0.0)],
    )
    t2 = MonitoringTable(
        monitoring_item="立柱沉降", monitor_date="2026-05-12",
        points=[MeasurementPoint(point_id="LZ1", cumulative_change=1.5, current_change=0.5)],
    )
    report = MonitoringReport(tables=[t1, t2])
    issues = run_calculation_checks(report)
    cross_issues = [i for i in issues if i.field_name == "跨期累计连续性"]
    assert len(cross_issues) == 0
```

## 风险评估

- 与现有 `(current - initial)` 验证**互补**，不冲突
- 跨期累计 5% 相对容差，避免 OCR 抖动假阳性
- 仅当同 monitoring_item 有 ≥2 期才触发（向后兼容）
- 不影响深层位移类（已通过 `deep_points` 路径）
