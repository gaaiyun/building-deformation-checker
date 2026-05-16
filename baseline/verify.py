# -*- coding: utf-8 -*-
"""
Manual ground-truth verification for 监测报告检查（测试）.
- Tolerances:
    * sedimentation/horizontal disp:  +/- 0.15 mm,  +/- 0.05 mm/d
    * cumulative > 10 mm: relax 5%
"""
from __future__ import annotations
from dataclasses import dataclass

INTERVAL_D = 10  # 2024-03-17 -> 2024-03-26
TOL_MM = 0.15
TOL_RATE = 0.05
RELAX_PCT = 0.05  # 5% when cumulative > 10 mm

results = []  # collected error/warning records


def chk(label, expected, actual, kind="mm", cumulative_for_relax=None):
    """Compare expected (computed) vs actual (reported)."""
    diff = abs(expected - actual)
    if kind == "mm":
        tol = TOL_MM
        if cumulative_for_relax is not None and abs(cumulative_for_relax) > 10:
            tol = max(tol, abs(cumulative_for_relax) * RELAX_PCT)
    elif kind == "rate":
        tol = TOL_RATE
    else:
        tol = 0.001
    status = "OK" if diff <= tol else "ERR"
    if status == "ERR":
        results.append((label, expected, actual, diff, tol, status))
    return status, diff, tol


# -----------------------------------------------------------------
# TABLE 1 — 支护结构顶部水平位移 (page 7+8)
# Header note: 累计变化量列 == 本次断面距离 since 初始断面距离 = 0
# 本次变化量 = 本次断面距离 - (上次断面距离)  — we cannot recompute without 上次值.
# We CAN verify: 累计变化量 vs (本次距离 - 初始) and 速率 vs 本次变化量/10d
# -----------------------------------------------------------------
print("=" * 70)
print("TABLE 1: 支护结构顶部水平位移  (page 7-8)")
print("=" * 70)
horiz = [
    # id, init, current, this_change, cum, rate
    ("1S10", 0.0,  35.4, -0.6,  35.4, -0.06),
    ("2S11", 0.0,  36.6, -4.0,  36.6, -0.40),
    ("S1",   0.0,   2.2, -2.4,   2.2, -0.24),
    ("S2",   0.0,   2.1, -2.7,   2.1, -0.27),
    ("S3",   0.0,   5.6,  0.8,   5.6,  0.08),
    ("S4",   0.0,  -3.3, -3.2,  -3.3, -0.32),
    ("S5",   0.0, -23.6,  2.7, -23.6,  0.27),
    ("S6",   0.0, -14.0,  1.9, -14.0,  0.11),  # 1.9/10 = 0.19, reported 0.11
    ("S7",   0.0,  13.2,  8.2,  13.2,  0.82),
    ("S8",   0.0,   0.7,  5.0,   0.7,  0.50),
    ("S9",   0.0,   8.1,  3.1,   8.1,  0.31),
]
for pid, init, cur, this, cum, rate in horiz:
    # Cumulative check
    cum_exp = cur - init
    chk(f"T1-{pid}-累计", cum_exp, cum, kind="mm", cumulative_for_relax=cum)
    # Rate check: rate = this_change / 10
    rate_exp = this / INTERVAL_D
    chk(f"T1-{pid}-速率", rate_exp, rate, kind="rate")
    print(f"{pid:>5} | cur={cur:7.2f}  cum_exp={cum_exp:7.2f} vs {cum:7.2f}  | rate_exp={rate_exp:7.3f} vs {rate:7.3f}")

# Statistics for T1
T1_cum = {p[0]: p[4] for p in horiz}
T1_rate = {p[0]: p[5] for p in horiz}
T1_this = {p[0]: p[3] for p in horiz}

max_pos_id  = max(T1_cum, key=lambda k: T1_cum[k])
max_neg_id  = min(T1_cum, key=lambda k: T1_cum[k])
max_rate_id = max(T1_rate, key=lambda k: abs(T1_rate[k]))
print(f"\nReported:  pos-max=2S11(36.6), neg-max=S5(-23.6), rate-max=S7(0.82)")
print(f"Computed:  pos-max={max_pos_id}({T1_cum[max_pos_id]}), neg-max={max_neg_id}({T1_cum[max_neg_id]}), rate-max(abs)={max_rate_id}({T1_rate[max_rate_id]})")


# -----------------------------------------------------------------
# TABLE 2 — 支护结构顶部竖向位移 (page 9)
# 初始高程 m, 本次高程 m; 本次变化量 mm, 累计变化量 mm, 速率 mm/d
# (本次高程 - 初始高程)*1000 = 累计变化量
# 速率 = 本次变化量 / 10
# -----------------------------------------------------------------
print("\n" + "=" * 70)
print("TABLE 2: 支护结构顶部竖向位移  (page 9)")
print("=" * 70)
vert = [
    ("S1", -2.70184, -2.70242, -1.85, 31.21, -0.185),
    ("S2", -2.71238, -2.71158, -0.46, 33.92, -0.046),
    ("S3", -2.71405, -2.70533,  8.72, 42.13,  0.484),  # rate 8.72/10=0.872 vs 0.484
    ("S4", -2.68907, -2.68797,  0.49, 28.06,  0.049),  # 1.10 -> 0.49 mismatch
    ("S5", -2.60719, -2.60778, -1.65, 26.50, -0.165),  # -0.59 vs -1.65 mismatch
    ("S6", -1.63570, -1.63453,  1.17, 27.49,  0.065),  # 1.17/10=0.117 vs 0.065
    ("S7", -1.69993, -1.69741,  1.83,  7.40,  0.183),  # cum=2.52 vs reported 7.40
    ("S8", -2.02196, -2.02058,  1.16, 18.09,  0.116),  # cum=1.38 vs 18.09
    ("S9", -1.92769, -1.92925,  1.60, 20.73,  0.160),  # cum=-1.56 vs 20.73 (sign)
]
for pid, h0, h1, this, cum, rate in vert:
    cum_exp = (h1 - h0) * 1000.0
    rate_exp = this / INTERVAL_D
    s1, d1, t1 = chk(f"T2-{pid}-累计", cum_exp, cum, kind="mm", cumulative_for_relax=cum)
    s2, d2, t2 = chk(f"T2-{pid}-速率", rate_exp, rate, kind="rate")
    print(f"{pid:>3} | h0={h0:.5f}->h1={h1:.5f} cum_exp={cum_exp:+7.2f} vs {cum:+7.2f} [{s1} d={d1:.2f}/t={t1:.2f}] | rate_exp={rate_exp:+.3f} vs {rate:+.3f} [{s2}]")

T2_cum = {p[0]: p[4] for p in vert}
T2_rate = {p[0]: p[5] for p in vert}
# Note: convention: cum "+" = up, "-" = sink
print(f"\nReported:  pos-max=S3(42.13), neg-max=S7(7.40), rate-max=S3(0.484)")
print(f"Reported neg-max claims S7=7.40 but 7.40 is POSITIVE → semantics check.")
print(f"All cum values are POSITIVE → no point in negative direction (no sinking)?")
# Reported: 简报 says 负方向最大=7.40mm/S7 — but the convention "-为下沉" means cum >0 = 上升.
# All cum >0 → no negative-direction point. But report still picks S7 (smallest positive) as 负方向最大.
# This is an interpretation issue — the data show no settlement (all uplift).

# Cross-check 简报 entry vs T2 row table
print(f"\nNote on T2: every cum is POSITIVE (uplift). 'Sinking max' should be the row closest to 0 with cum>0, OR there is none.")


# -----------------------------------------------------------------
# TABLE 3 — 周边地面沉降 (page 10)
# -----------------------------------------------------------------
print("\n" + "=" * 70)
print("TABLE 3: 周边地面沉降  (page 10)")
print("=" * 70)
ground = [
    ("D1",  9.62118,  9.59143, -0.19, -29.75, -0.019),
    ("D2",  9.51032,  9.48291, -0.10, -27.41, -0.010),
    ("D4",  9.85360,  9.82237, -0.46, -31.23, -0.046),
    ("D5", 10.11699, 10.08640, -1.56, -30.59, -0.156),
]
for pid, h0, h1, this, cum, rate in ground:
    cum_exp = (h1 - h0) * 1000.0
    rate_exp = this / INTERVAL_D
    s1, d1, t1 = chk(f"T3-{pid}-累计", cum_exp, cum, kind="mm", cumulative_for_relax=cum)
    s2, d2, t2 = chk(f"T3-{pid}-速率", rate_exp, rate, kind="rate")
    print(f"{pid:>3} | cum_exp={cum_exp:+7.2f} vs {cum:+7.2f} [{s1}] | rate_exp={rate_exp:+.3f} vs {rate:+.3f} [{s2}]")

T3_cum = {p[0]: p[4] for p in ground}
T3_rate = {p[0]: p[5] for p in ground}
print(f"\nReported:  pos-max=D2(-27.41), neg-max=D4(-31.23), rate-max=D2(-0.010)")
print(f"All cum are NEGATIVE → 'pos-max' = closest-to-0 = D2(-27.41). 'neg-max' = most negative = D4(-31.23). OK.")
print(f"Rate: smallest |rate| = D2(-0.010); largest |rate| = D5(-0.156). Report picks D2 as 'max-rate' = wrong; should be D5 by magnitude.")


# -----------------------------------------------------------------
# TABLE 4 — 管线沉降 (page 11)
# -----------------------------------------------------------------
print("\n" + "=" * 70)
print("TABLE 4: 管线沉降  (page 11)")
print("=" * 70)
pipe = [
    ("G1",  9.63398,  9.60495, -0.08, -29.03, -0.008),
    ("G2",  9.51112,  9.52275, -0.26, -17.45, -0.026),  # h1>h0 but cum is -17.45?
    ("G4",  9.90557,  9.88204, -1.44, -23.53, -0.144),
    ("G5", 10.13768, 10.11800, -1.81, -19.68, -0.181),
]
for pid, h0, h1, this, cum, rate in pipe:
    cum_exp = (h1 - h0) * 1000.0
    rate_exp = this / INTERVAL_D
    s1, d1, t1 = chk(f"T4-{pid}-累计", cum_exp, cum, kind="mm", cumulative_for_relax=cum)
    s2, d2, t2 = chk(f"T4-{pid}-速率", rate_exp, rate, kind="rate")
    print(f"{pid:>3} | h0={h0:.5f}->h1={h1:.5f} cum_exp={cum_exp:+7.2f} vs {cum:+7.2f} [{s1}] | rate_exp={rate_exp:+.3f} vs {rate:+.3f} [{s2}]")

T4_cum = {p[0]: p[4] for p in pipe}
T4_rate = {p[0]: p[5] for p in pipe}
print(f"\nReported:  pos-max=G2(-17.45), neg-max=G1(-29.03), rate-max=G1(-0.008)")
print(f"All cum negative → 'pos-max' (closest to 0) = G2(-17.45) OK; 'neg-max' (most negative) = G1(-29.03) OK.")
print(f"Rate: |G5|=0.181 largest; report picks G1(0.008) smallest → wrong-direction or wrong-pick.")


# -----------------------------------------------------------------
# TABLE 5 — 地下水位 (page 12)
# Columns: 初始水位深度 mm, 本次水位深度 mm, 本次变化量 mm, 累计变化量 mm, 速率 mm/d
# Cumulative = 本次 - 初始
# 速率 = 本次变化量 / 10
# -----------------------------------------------------------------
print("\n" + "=" * 70)
print("TABLE 5: 地下水位  (page 12)")
print("=" * 70)
water = [
    ("W1", -4266, -3039,  -247,  -130, -24.7),
    ("W3", -4327, -4198, -1700, -1250, -170.0),
    ("W4", -4844, -3947, -1768,    18, -176.8),
    ("W5", -4828, -4591, -1723,  -661, -172.3),
    ("W6", -5065, -3894,    68,  1906,    6.8),
]
for pid, d0, d1, this, cum, rate in water:
    cum_exp = d1 - d0
    rate_exp = this / INTERVAL_D
    s1, d_, t_ = chk(f"T5-{pid}-累计", cum_exp, cum, kind="mm", cumulative_for_relax=cum)
    s2, dd, tt = chk(f"T5-{pid}-速率", rate_exp, rate, kind="rate")
    print(f"{pid:>3} | d0={d0} d1={d1} cum_exp={cum_exp:+6d} vs {cum:+6d} [{s1} diff={d_:.1f}/tol={t_:.1f}] | rate_exp={rate_exp:+.2f} vs {rate:+.2f} [{s2}]")

T5_cum = {p[0]: p[4] for p in water}
T5_rate = {p[0]: p[5] for p in water}
print(f"\nReported:  pos-max=W6(1906), neg-max=W3(-1250), rate-max=W4(176.8)")
print(f"Computed cumulative max-pos: {max(T5_cum, key=lambda k:T5_cum[k])}({max(T5_cum.values())})")
print(f"Computed cumulative max-neg: {min(T5_cum, key=lambda k:T5_cum[k])}({min(T5_cum.values())})")


# -----------------------------------------------------------------
# TABLE 6 — 锚索拉力 (page 13)
# Columns: 初始内力 kN, 本次内力 kN, 本次变化量 kN, 累计变化量 kN
# Cumulative = 本次 - 初始
# -----------------------------------------------------------------
print("\n" + "=" * 70)
print("TABLE 6: 锚索拉力  (page 13)")
print("=" * 70)
anchor = [
    ("M3", 172.8, 178.7,  -0.3,  5.9),
    ("M4", 193.6, 192.7,  -0.4, -0.9),
    ("M5", 216.6, 214.9, -23.9, -1.7),
    ("M8", 165.3, 167.4,  -0.2,  2.1),
    ("M9", 202.3, 202.9,   0.2,  0.6),
]
for pid, f0, f1, this, cum in anchor:
    cum_exp = f1 - f0
    chk(f"T6-{pid}-累计", cum_exp, cum, kind="mm", cumulative_for_relax=cum)
    print(f"{pid:>3} | f0={f0} f1={f1} cum_exp={cum_exp:+.2f} vs {cum:+.2f}")

T6_max_pid = max(anchor, key=lambda r: r[2])[0]
T6_min_pid = min(anchor, key=lambda r: r[2])[0]
print(f"\nReported:  max=M5(214.9), min=M8(167.4)")
print(f"Computed:  max-本次内力={max(anchor, key=lambda r:r[2])[0]}({max(r[2] for r in anchor)})")
print(f"           min-本次内力={min(anchor, key=lambda r:r[2])[0]}({min(r[2] for r in anchor)})")


# -----------------------------------------------------------------
# TABLE 7 — 深层水平位移 C1 (page 14) — 24 pts
# Columns: 测点深度 m, 上次累计量 mm, 本次累计量 mm, 变化速率 mm/d
# 速率 = (本次 - 上次)/10
# -----------------------------------------------------------------
print("\n" + "=" * 70)
print("TABLE 7: 深层水平位移 C1  (page 14)")
print("=" * 70)
c1 = [
    (-0.5, 0.40, 0.59, 0.019),
    (-1,   0.39, 0.48, 0.009),
    (-1.5, 0.45, 0.40, 0.005),
    (-2,   0.54, 0.41, 0.013),
    (-2.5, 0.48, 0.56, 0.008),
    (-3,   0.43, 0.61, 0.018),
    (-3.5, 0.26, 0.56, 0.030),
    (-4,   0.32, 0.53, 0.021),
    (-4.5, 0.30, 0.44, 0.014),
    (-5,   0.39, 0.53, 0.014),
    (-5.5, 0.34, 0.62, 0.028),
    (-6,   0.49, 0.70, 0.021),
    (-6.5, 0.38, 0.68, 0.030),
    (-7,   0.49, 0.56, 0.007),
    (-7.5, 0.56, 0.50, 0.006),
    (-8,   0.37, 0.29, 0.008),
    (-8.5, 0.27, 0.33, 0.006),
    (-9,   0.37, 0.25, 0.012),
    (-9.5, 0.32, 0.43, 0.011),
    (-10,  0.35, 0.52, 0.017),
    (-10.5, 0.24, 0.25, 0.001),
    (-11,  0.25, 0.30, 0.005),
    (-11.5, 0.19, 0.22, 0.003),
    (-12,  0.02, 0.03, 0.001),
]
for d, prev, cur, rate in c1:
    rate_exp = abs(cur - prev) / INTERVAL_D
    s, dd, t = chk(f"T7-C1-{d}-速率", rate_exp, rate, kind="rate")
    flag = "" if s == "OK" else f"  <<{s} exp={rate_exp:.3f}>>"
    print(f"d={d:6} prev={prev:.2f} cur={cur:.2f} rate_rep={rate:.3f} rate_calc={rate_exp:.3f}{flag}")

# Statistics for C1
c1_vals = [r[2] for r in c1]
pos_c1 = max(c1, key=lambda r: r[2])
neg_c1 = min(c1, key=lambda r: r[2])
rate_max_c1 = max(c1, key=lambda r: abs(r[3]))
print(f"\nT7 stats: pos-max depth={pos_c1[0]} val={pos_c1[2]}, neg-min depth={neg_c1[0]} val={neg_c1[2]}, rate-max depth={rate_max_c1[0]} val={rate_max_c1[3]}")


# -----------------------------------------------------------------
# TABLE 8 — 深层水平位移 C10 (page 15) — 19 pts, no stats block
# -----------------------------------------------------------------
print("\n" + "=" * 70)
print("TABLE 8: 深层水平位移 C10  (page 15)  — note: header says 19 个, table has 20 rows")
print("=" * 70)
c10 = [
    (-0.5, 0.56, 0.66, 0.010),
    (-1,   0.57, 0.67, 0.010),
    (-1.5, 0.48, 0.69, 0.021),
    (-2,   0.53, 0.74, 0.021),
    (-2.5, 0.53, 0.71, 0.018),
    (-3,   0.43, 0.60, 0.017),
    (-3.5, 0.38, 0.59, 0.021),
    (-4,   0.37, 0.57, 0.020),
    (-4.5, 0.50, 0.58, 0.008),
    (-5,   0.41, 0.49, 0.008),
    (-5.5, 0.34, 0.43, 0.009),
    (-6,   0.33, 0.42, 0.009),
    (-6.5, 0.32, 0.39, 0.007),
    (-7,  -0.02, 0.06, 0.008),
    (-7.5, 0.00, 0.01, 0.001),
    (-8,   0.05, 0.05, 0.000),
    (-8.5,-0.02,-0.01, 0.001),
    (-9,   0.03, 0.03, 0.000),
    (-9.5, 0.02, 0.02, 0.000),
]
print(f"Row count = {len(c10)} (header says 19个 → OK)")
for d, prev, cur, rate in c10:
    rate_exp = abs(cur - prev) / INTERVAL_D
    s, dd, t = chk(f"T8-C10-{d}-速率", rate_exp, rate, kind="rate")
    flag = "" if s == "OK" else f"  <<{s} exp={rate_exp:.3f}>>"
    print(f"d={d:6} prev={prev:.2f} cur={cur:.2f} rate_rep={rate:.3f} rate_calc={rate_exp:.3f}{flag}")

pos_c10 = max(c10, key=lambda r: r[2])
neg_c10 = min(c10, key=lambda r: r[2])
print(f"\nT8 stats: pos-max depth={pos_c10[0]} val={pos_c10[2]}; neg-min depth={neg_c10[0]} val={neg_c10[2]}")
# Brief claims C10 正方向最大=0.74mm; T8 pos-max should be at d=-2 cur=0.74 → consistent

# -----------------------------------------------------------------
# TABLE 9 — 深层水平位移 C11 (page 16) — 10 pts, no stats block
# -----------------------------------------------------------------
print("\n" + "=" * 70)
print("TABLE 9: 深层水平位移 C11  (page 16)")
print("=" * 70)
c11 = [
    (-0.5,  0.24, 0.47, 0.023),
    (-1,    0.02, 0.34, 0.032),
    (-1.5, -0.12, 0.18, 0.030),
    (-2,   -0.04, 0.20, 0.024),
    (-2.5, -0.08, 0.21, 0.029),
    (-3,    0.02, 0.30, 0.028),
    (-3.5,  0.00, 0.27, 0.027),
    (-4,    0.08, 0.33, 0.025),
    (-4.5,  0.07, 0.14, 0.007),
    (-5,    0.00, 0.00, 0.000),
]
for d, prev, cur, rate in c11:
    rate_exp = abs(cur - prev) / INTERVAL_D
    s, dd, t = chk(f"T9-C11-{d}-速率", rate_exp, rate, kind="rate")
    flag = "" if s == "OK" else f"  <<{s} exp={rate_exp:.3f}>>"
    print(f"d={d:6} prev={prev:.2f} cur={cur:.2f} rate_rep={rate:.3f} rate_calc={rate_exp:.3f}{flag}")


# -----------------------------------------------------------------
# TABLE 10 — 深层水平位移 C4 (page 17) — 24 pts
# -----------------------------------------------------------------
print("\n" + "=" * 70)
print("TABLE 10: 深层水平位移 C4  (page 17)")
print("=" * 70)
c4 = [
    (-0.5, -1.06, -0.93, 0.013),
    (-1,   -0.98, -0.83, 0.015),
    (-1.5, -1.18, -0.63, 0.055),
    (-2,   -0.96, -0.36, 0.060),
    (-2.5, -0.72, -0.16, 0.056),
    (-3,   -0.75, -0.15, 0.060),
    (-3.5, -0.66, -0.01, 0.065),
    (-4,   -0.63,  0.07, 0.070),
    (-4.5, -0.74, -0.09, 0.065),
    (-5,   -0.90, -0.13, 0.077),
    (-5.5, -0.79, -0.10, 0.069),
    (-6,   -0.73, -0.03, 0.070),
    (-6.5, -0.79, -0.25, 0.054),
    (-7,   -0.82, -0.27, 0.055),
    (-7.5, -0.82, -0.19, 0.063),
    (-8,   -0.71, -0.10, 0.061),
    (-8.5, -0.44, -0.13, 0.031),
    (-9,   -0.37, -0.54, 0.017),
    (-9.5, -0.37, -0.56, 0.019),
    (-10,  -0.23, -0.50, 0.027),
    (-10.5,-0.06, -0.16, 0.010),
    (-11,  -0.14, -0.16, 0.002),
    (-11.5,-0.02, -0.02, 0.000),
    (-12,   0.00,  0.00, 0.000),
]
for d, prev, cur, rate in c4:
    rate_exp = abs(cur - prev) / INTERVAL_D
    s, dd, t = chk(f"T10-C4-{d}-速率", rate_exp, rate, kind="rate")
    flag = "" if s == "OK" else f"  <<{s} exp={rate_exp:.3f}>>"
    print(f"d={d:6} prev={prev:+.2f} cur={cur:+.2f} rate_rep={rate:.3f} rate_calc={rate_exp:.3f}{flag}")

pos_c4 = max(c4, key=lambda r: r[2])
neg_c4 = min(c4, key=lambda r: r[2])
rate_max_c4 = max(c4, key=lambda r: abs(r[3]))
print(f"\nT10 stats: pos-max depth={pos_c4[0]} val={pos_c4[2]}; neg-min depth={neg_c4[0]} val={neg_c4[2]}; rate-max depth={rate_max_c4[0]} val={rate_max_c4[3]}")
print(f"Reported: pos-max depth=4 val=0.07; neg-min depth=0.5 val=-0.93; rate-max depth=5 val=0.077")


# -----------------------------------------------------------------
# TABLE 11 — 深层水平位移 C5 (page 18) — 16 pts (header), table has 16
# -----------------------------------------------------------------
print("\n" + "=" * 70)
print("TABLE 11: 深层水平位移 C5  (page 18)")
print("=" * 70)
c5 = [
    (-0.5,  0.03, 0.00, 0.003),
    (-1,    0.03, 0.05, 0.002),
    (-1.5,  0.05, 0.57, 0.052),
    (-2,    0.15, 0.52, 0.037),
    (-2.5,  0.08, 0.37, 0.029),
    (-3,    0.22, 0.32, 0.010),
    (-3.5,  0.10, 0.33, 0.023),
    (-4,    0.09, 0.49, 0.040),
    (-4.5,  0.08, 0.24, 0.016),
    (-5,    0.08, 0.24, 0.016),
    (-5.5,  0.06, 0.22, 0.016),
    (-6,    0.13, 0.12, 0.001),
    (-6.5,  0.10, 0.01, 0.009),
    (-7,    0.08, 0.02, 0.006),
    (-7.5, -0.03, 0.04, 0.007),
    (-8,    0.03, 0.03, 0.000),
]
for d, prev, cur, rate in c5:
    rate_exp = abs(cur - prev) / INTERVAL_D
    s, dd, t = chk(f"T11-C5-{d}-速率", rate_exp, rate, kind="rate")
    flag = "" if s == "OK" else f"  <<{s} exp={rate_exp:.3f}>>"
    print(f"d={d:6} prev={prev:+.2f} cur={cur:+.2f} rate_rep={rate:.3f} rate_calc={rate_exp:.3f}{flag}")


# -----------------------------------------------------------------
# SUMMARY
# -----------------------------------------------------------------
print("\n" + "=" * 70)
print("ALL CALC-LEVEL FAILURES")
print("=" * 70)
for label, exp, act, diff, tol, status in results:
    print(f"  {status}  {label:35} exp={exp:+9.4f}  rep={act:+9.4f}  diff={diff:.4f}  tol={tol:.4f}")
print(f"\nTotal calc failures: {len(results)}")
