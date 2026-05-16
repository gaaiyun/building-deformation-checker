# -*- coding: utf-8 -*-
"""
Re-examine the column semantics.
Hypothesis:
  '初始高程' = project-start baseline (not previous survey)
  '本次高程' = current survey elevation
  '本次变化量' = current - previous (not - initial)
  '累计变化量' = current - initial (since project start)
  '变化速率' = 本次变化量 / interval

Validation rules:
  ground/pipe/vert: cum = (h_curr - h_initial) * 1000
  rate = this / interval (10 days)
  this = curr_in_mm - prev_in_mm (cannot recompute, but rate*10 should == this)
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

INTERVAL = 10

print("=== T3 周边地面沉降 (page 10) verify cum vs (curr-initial)*1000 ===")
T3 = [
    ("D1",  9.62118,  9.59143, -0.19, -29.75, -0.019),
    ("D2",  9.51032,  9.48291, -0.10, -27.41, -0.010),
    ("D4",  9.85360,  9.82237, -0.46, -31.23, -0.046),
    ("D5", 10.11699, 10.08640, -1.56, -30.59, -0.156),
]
for p, h0, h1, this, cum, rate in T3:
    cum_calc = (h1 - h0) * 1000
    rate_calc = this / INTERVAL
    print(f"{p}: cum_calc={cum_calc:+8.2f}  cum_rep={cum:+8.2f}  | this={this:+.2f}  rate={rate:+.3f}  rate_calc={rate_calc:+.3f}")

# Note: D1 cum_calc = -29.75 matches -29.75 exactly. The (h1-h0)*1000 IS the cumulative.
# This confirms: '初始高程' = baseline since project start; 'cum' = curr - initial; OK.
# And 'this' (本次变化量) cannot be derived from these columns; rate=this/10 holds. OK.

print("\n=== T2 支护结构顶部竖向位移 (page 9) ===")
T2 = [
    ("S1", -2.70184, -2.70242, -1.85, 31.21, -0.185),
    ("S2", -2.71238, -2.71158, -0.46, 33.92, -0.046),
    ("S3", -2.71405, -2.70533,  8.72, 42.13,  0.484),
    ("S4", -2.68907, -2.68797,  0.49, 28.06,  0.049),
    ("S5", -2.60719, -2.60778, -1.65, 26.50, -0.165),
    ("S6", -1.63570, -1.63453,  1.17, 27.49,  0.065),
    ("S7", -1.69993, -1.69741,  1.83,  7.40,  0.183),
    ("S8", -2.02196, -2.02058,  1.16, 18.09,  0.116),
    ("S9", -1.92769, -1.92925,  1.60, 20.73,  0.160),
]
for p, h0, h1, this, cum, rate in T2:
    cum_calc = (h1 - h0) * 1000
    rate_calc = this / INTERVAL
    print(f"{p}: cum_calc={cum_calc:+8.2f}  cum_rep={cum:+8.2f}  | this={this:+.2f}  rate={rate:+.3f}  rate_calc={rate_calc:+.3f}")

# CRITICAL ANALYSIS:
# For T2, cum != (h1-h0)*1000 → '初始高程' here is the previous survey's elevation, NOT project baseline.
# Reason: cum captures full history. 本次高程 is fresh. 'rate' tracks this/10. 'this' = current - previous (not derivable).
# So '初始高程' has different meaning across tables → inconsistent OCR or different report convention.

# For T2, we can ONLY check rate = this / 10
print("\n=== T2 rate check ===")
errs = 0
for p, h0, h1, this, cum, rate in T2:
    rate_calc = this / INTERVAL
    ok = abs(rate_calc - rate) <= 0.05
    if not ok:
        print(f"  {p}: this={this}, rate_calc={rate_calc:.3f}, rate_rep={rate:.3f}  ERR")
        errs += 1
print(f"T2 rate errors: {errs} / {len(T2)}")
# Note: S3 fails: 8.72/10 = 0.872 but reported 0.484; S6 fails: 1.17/10=0.117 vs 0.065 — these are real anomalies.


print("\n=== T4 管线沉降 (page 11) ===")
T4 = [
    ("G1",  9.63398,  9.60495, -0.08, -29.03, -0.008),
    ("G2",  9.51112,  9.52275, -0.26, -17.45, -0.026),
    ("G4",  9.90557,  9.88204, -1.44, -23.53, -0.144),
    ("G5", 10.13768, 10.11800, -1.81, -19.68, -0.181),
]
for p, h0, h1, this, cum, rate in T4:
    cum_calc = (h1 - h0) * 1000
    rate_calc = this / INTERVAL
    print(f"{p}: cum_calc={cum_calc:+8.2f}  cum_rep={cum:+8.2f}  | this={this:+.2f}  rate={rate:+.3f}  rate_calc={rate_calc:+.3f}")

# G1: (-29.03 vs -29.03) - exact match → here '初始高程' = project baseline (different from T2!)
# G4: -23.53 vs -23.53 match.
# G5: -19.68 vs -19.68 match.
# G2: cum_calc = +11.63 (h1>h0) but cum_rep = -17.45 — major contradiction.
# So for T4, the column convention is "initial = project baseline" (like T3), BUT G2 row has bad data.
# Specifically: G2 h0=9.51112, h1=9.52275 → h1>h0 means elevation rose 11.63 mm; but report says cum -17.45 (sank).
# this column also says -0.26 (sank this period) — which conflicts with h1>h0 if h1 is current.
# Conclusion: G2 has internal contradiction; OCR or report data error. Most likely h1 OCR error.

print("\n=== T5 地下水位 (page 12) — units in mm, no m conversion ===")
T5 = [
    ("W1", -4266, -3039,  -247,  -130, -24.7),
    ("W3", -4327, -4198, -1700, -1250, -170.0),
    ("W4", -4844, -3947, -1768,    18, -176.8),
    ("W5", -4828, -4591, -1723,  -661, -172.3),
    ("W6", -5065, -3894,    68,  1906,    6.8),
]
for p, d0, d1, this, cum, rate in T5:
    cum_calc = d1 - d0
    rate_calc = this / INTERVAL
    print(f"{p}: d0={d0:>6} d1={d1:>6}  cum_calc={cum_calc:+6d}  cum_rep={cum:+6d}  | this={this:+5d}  rate={rate:+8.2f}  rate_calc={rate_calc:+.2f}")

# For T5, cum_calc rarely matches cum_rep. So '初始水位深度' here is NOT (curr - initial baseline).
# Rate check: rate = this / 10 in ALL rows.
# Check: W1: rate=-24.7; this/10 = -247/10 = -24.7 OK.
#        W3: this=-1700, rate=-170 OK
#        W6: this=68, rate=6.8 OK
# Rate computation is consistent. Cum cannot be recomputed from these two columns alone.

# This tells us: the report uses different conventions for "初始" across tables.
# In T3, T4: 初始高程 = project-baseline (cum = (h1-h0)*1000 works)
# In T2, T5: 初始 column is something else (probably previous survey, or a non-baseline reference)
# OR: the values in the "初始" column for T2 & T5 are just current-period start values, and
# 累计 column comes from a separately-maintained history.
# Either way, we can only verify the formulas where they apply: T3, T4, T6, plus rates everywhere.

print("\n=== Magnitudes test for w-level (very large rates, 5mm/d threshold!) ===")
# Threshold for 地下水位: 报警 2500mm, 控制 3000mm, 速率 500mm/d
# All rates < 500mm/d → 'normal' but |rate| for W3..W5 is ~170 mm/d — close to flagging
# Brief says all "正常". Fine. But these are very large per-day moves.

# Brief's 速率 max: W4(176.8 mm/d).  |rate| max is W4=176.8 (smallest is W6=6.8). OK.

print("\n=== Brief simulator: verify simbrief row max picks ===")

briefs = {
    "支护结构顶部水平位移": dict(
        neg_max=("S5", -23.6), pos_max=("2S11", 36.6), rate_max=("S7", 0.82)
    ),
    "支护结构顶部竖向位移": dict(
        neg_max=("S7", 7.40), pos_max=("S3", 42.13), rate_max=("S3", 0.484)
    ),
    "周边地面沉降": dict(
        neg_max=("D4", -31.23), pos_max=("D2", -27.41), rate_max=("D2", -0.010)
    ),
    "管线沉降": dict(
        neg_max=("G1", -29.03), pos_max=("G2", -17.45), rate_max=("G1", -0.008)
    ),
    "地下水位": dict(
        neg_max=("W3", -1250), pos_max=("W6", 1906), rate_max=("W4", 176.8)
    ),
    "深层水平位移观测": dict(
        neg_max=("C4", -0.93), pos_max=("C10", 0.74), rate_max=("C4", 0.077)
    ),
    "锚索拉力": dict(
        max_force=("M5", 214.9), min_force=("M3", 178.7)
    ),
}

# Cross-validate with sub-tables
print("\nBrief vs Table 1 stats (页7-8): brief says neg-max=S5(-23.6) (OK), pos-max=2S11(36.6) (OK), rate-max=S7(0.82) (OK)")
print("Brief vs Table 2 stats (页9): brief says neg-max=S7(7.40), pos-max=S3(42.13), rate-max=S3(0.484)")
print("  All cum in T2 are POSITIVE (uplift); the 'neg-max' label is misapplied; 7.40 is the smallest positive, not negative.")
print("  Per备注 convention: '-'=sink, '+'=up. There's NO sinking point. So 负方向最大 has no valid row → label should be N/A or 0.")

print("\nBrief vs Table 3 stats (页10): brief rate-max=D2(-0.010). |D5|=0.156 is larger. ERROR.")
print("  Table 3 also reports rate-max=D2 → so the table itself has the wrong selection; brief is consistent with the table but both are wrong.")

print("\nBrief vs Table 4 stats (页11): brief rate-max=G1(-0.008). |G5|=0.181 is larger. ERROR.")
print("  Table 4 also reports rate-max=G1 → table itself is wrong.")

print("\nBrief vs Table 5 stats (页12): brief rate-max=W4(176.8). |W4|=176.8 is largest by magnitude. OK.")

print("\nBrief vs Table 6 (锚索拉力, 页13): brief says 负方向最大=214.9kN/M5 and 正方向最大=178.7kN/M3.")
print("  Anchor table has 最大内力/最小内力 (not pos/neg directional). Brief mis-categorizes as pos/neg.")
print("  Brief value M5=214.9 = max force (correct value), M3=178.7 != min (M8=167.4 is min). So brief picks wrong row for 'min'.")

print("\nBrief vs Tables 7-11 (深层水平位移): brief picks across all 5 测孔, claims overall:")
print("  neg-max=C4(-0.93), pos-max=C10(0.74), rate-max=C4(0.077)")
print("  Compute global from all bores:")
all_deep = []
# C1 (T7)
c1 = [(-0.5, 0.40, 0.59, 0.019),(-1, 0.39, 0.48, 0.009),(-1.5, 0.45, 0.40, 0.005),(-2, 0.54, 0.41, 0.013),(-2.5, 0.48, 0.56, 0.008),(-3, 0.43, 0.61, 0.018),(-3.5, 0.26, 0.56, 0.030),(-4, 0.32, 0.53, 0.021),(-4.5, 0.30, 0.44, 0.014),(-5, 0.39, 0.53, 0.014),(-5.5, 0.34, 0.62, 0.028),(-6, 0.49, 0.70, 0.021),(-6.5, 0.38, 0.68, 0.030),(-7, 0.49, 0.56, 0.007),(-7.5, 0.56, 0.50, 0.006),(-8, 0.37, 0.29, 0.008),(-8.5, 0.27, 0.33, 0.006),(-9, 0.37, 0.25, 0.012),(-9.5, 0.32, 0.43, 0.011),(-10, 0.35, 0.52, 0.017),(-10.5, 0.24, 0.25, 0.001),(-11, 0.25, 0.30, 0.005),(-11.5, 0.19, 0.22, 0.003),(-12, 0.02, 0.03, 0.001)]
for d, prev, cur, rate in c1:
    all_deep.append(("C1", d, cur, rate))
c10 = [(-0.5, 0.56, 0.66, 0.010),(-1, 0.57, 0.67, 0.010),(-1.5, 0.48, 0.69, 0.021),(-2, 0.53, 0.74, 0.021),(-2.5, 0.53, 0.71, 0.018),(-3, 0.43, 0.60, 0.017),(-3.5, 0.38, 0.59, 0.021),(-4, 0.37, 0.57, 0.020),(-4.5, 0.50, 0.58, 0.008),(-5, 0.41, 0.49, 0.008),(-5.5, 0.34, 0.43, 0.009),(-6, 0.33, 0.42, 0.009),(-6.5, 0.32, 0.39, 0.007),(-7, -0.02, 0.06, 0.008),(-7.5, 0.00, 0.01, 0.001),(-8, 0.05, 0.05, 0.000),(-8.5, -0.02, -0.01, 0.001),(-9, 0.03, 0.03, 0.000),(-9.5, 0.02, 0.02, 0.000)]
for d, prev, cur, rate in c10:
    all_deep.append(("C10", d, cur, rate))
c11 = [(-0.5, 0.24, 0.47, 0.023),(-1, 0.02, 0.34, 0.032),(-1.5, -0.12, 0.18, 0.030),(-2, -0.04, 0.20, 0.024),(-2.5, -0.08, 0.21, 0.029),(-3, 0.02, 0.30, 0.028),(-3.5, 0.00, 0.27, 0.027),(-4, 0.08, 0.33, 0.025),(-4.5, 0.07, 0.14, 0.007),(-5, 0.00, 0.00, 0.000)]
for d, prev, cur, rate in c11:
    all_deep.append(("C11", d, cur, rate))
c4 = [(-0.5, -1.06, -0.93, 0.013),(-1, -0.98, -0.83, 0.015),(-1.5, -1.18, -0.63, 0.055),(-2, -0.96, -0.36, 0.060),(-2.5, -0.72, -0.16, 0.056),(-3, -0.75, -0.15, 0.060),(-3.5, -0.66, -0.01, 0.065),(-4, -0.63, 0.07, 0.070),(-4.5, -0.74, -0.09, 0.065),(-5, -0.90, -0.13, 0.077),(-5.5, -0.79, -0.10, 0.069),(-6, -0.73, -0.03, 0.070),(-6.5, -0.79, -0.25, 0.054),(-7, -0.82, -0.27, 0.055),(-7.5, -0.82, -0.19, 0.063),(-8, -0.71, -0.10, 0.061),(-8.5, -0.44, -0.13, 0.031),(-9, -0.37, -0.54, 0.017),(-9.5, -0.37, -0.56, 0.019),(-10, -0.23, -0.50, 0.027),(-10.5, -0.06, -0.16, 0.010),(-11, -0.14, -0.16, 0.002),(-11.5, -0.02, -0.02, 0.000),(-12, 0.00, 0.00, 0.000)]
for d, prev, cur, rate in c4:
    all_deep.append(("C4", d, cur, rate))
c5 = [(-0.5, 0.03, 0.00, 0.003),(-1, 0.03, 0.05, 0.002),(-1.5, 0.05, 0.57, 0.052),(-2, 0.15, 0.52, 0.037),(-2.5, 0.08, 0.37, 0.029),(-3, 0.22, 0.32, 0.010),(-3.5, 0.10, 0.33, 0.023),(-4, 0.09, 0.49, 0.040),(-4.5, 0.08, 0.24, 0.016),(-5, 0.08, 0.24, 0.016),(-5.5, 0.06, 0.22, 0.016),(-6, 0.13, 0.12, 0.001),(-6.5, 0.10, 0.01, 0.009),(-7, 0.08, 0.02, 0.006),(-7.5, -0.03, 0.04, 0.007),(-8, 0.03, 0.03, 0.000)]
for d, prev, cur, rate in c5:
    all_deep.append(("C5", d, cur, rate))

pos_glob = max(all_deep, key=lambda r: r[2])
neg_glob = min(all_deep, key=lambda r: r[2])
rate_glob = max(all_deep, key=lambda r: r[3])
print(f"  global pos-max: {pos_glob}  → bore={pos_glob[0]} cur={pos_glob[2]}")
print(f"  global neg-min: {neg_glob}  → bore={neg_glob[0]} cur={neg_glob[2]}")
print(f"  global rate-max: {rate_glob} → bore={rate_glob[0]} d={rate_glob[1]} rate={rate_glob[3]}")
