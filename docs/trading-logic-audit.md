# Trading logic audit and narrowly scoped repairs

This job did not establish a profitable strategy. It found and repaired an unfilled-plan lifecycle defect and added an explicit, versioned economic-planning mode. The original nearest-two target policy, stops, qualifications, hierarchy and risk limits remain unchanged. No production records or operating processes were accessed.

## Exact baseline and dependencies

Main `9da1d3cf7a8fcc902b734f964a30c95032ee8d0d`; PR46 `198c5eac7d0602fb9f948513a3be3e3381f65d8d`; PR74 `8798b4985aec1dcd4c115a6d0643de109e5ed1d2`; PR75 `c5bac5a2176f4c7855486823887f6f41318197ce`; PR76 `2adb4ee243b45d10d43df3acb61be193c86dc308`. All four actual head-SHA Python/JavaScript checks passed when inspected. PR46 is open/non-draft;74–76 are drafts. None is merged. This new branch is stacked on76, which depends on75 then74. It does not edit those branches or blindly merge46.

PR74 supplies the isolated lab;75 fixed string directions and protected modern research;76 preserves the completed zero-trade January comparison and negative strategy research. Those jobs/results are retained. Baseline public-head tests were1246 passed/1original strictxfail plus298 Node22 tests.

## Confirmed repairs

1. **Plans were spent before they filled.** The original simulator appended a source plan to `seen_source_plans` at signal generation. An occupied slot, entry-gap refusal, minimum-notional/capital refusal or missing-data cancellation could permanently suppress a later legitimate reevaluation. Six initial regression cases failed before repair. Consumption now happens only on a successful fill. Explicit pending/rejected/filled/closed/unresolved states and the last offered boundary survive SQLite checkpoints. A fresh later evaluation may retry an unfilled plan; a repeated boundary or already-filled plan cannot execute it twice. A filled plan remains consumed after its position closes. Different genuine plan IDs remain independent. No retry manufactures a signal: the caller must still independently qualify it at the new close.

2. **An optional cost-consistent planning cohort is now available.** Current main/76 v2 planning checks raw R only. Reviewed pure arithmetic from current46 is selectively adapted; its executable AST is unchanged, while the module introduction accurately describes the retained legacy scenario type. The new `economic-planning-research-v1` mode uses existing targets and stop and compares exact net reward with net stop loss, including adverse entry/exit fills and fees on both notionals. Both historical and forward services bind that mode to the exact cost scenario their report/paper plan records. A caller cannot certify under a different cheap scenario. Zero declared costs withhold certification; forward observations give an explicit economic refusal explanation.

3. **A rounded threshold was optimistic at an extreme precision boundary.** Actual current46 retained a target where exact net R was0.999999999996 because its display value rounded to1. The new mode compares exact monetary amounts before rounding, for both the raw configured floor and the net economic floor. This is a small mathematical correctness issue, not an explanation for January losses or a measurable profitability improvement. The current46 short-sign defect reported earlier is ALREADY fixed;43 independent current46 economic/history tests passed. It is not claimed as a new repair.

**Compatibility is explicit:** `PlanningParameters(economic_costs=None)` retains the original v2 fingerprint and byte-identical plan output. It is an ECONOMICALLY UNVERIFIED diagnostic policy. The new mode requires an explicit non-None scenario; it has different plan configuration/identity/rules-version material. Defaults were not silently activated on a running bot. Existing stored cohorts are never rewritten or retagged. The lab fingerprint now includes the actual shared cost source.

## All twelve rejected plans, reconstructed exactly

All13 distinct recorded plan snapshots were rebuilt from the same verified December2023/January2024 candles, fixed warmup and actual qualification/planning pipeline. Every original plan JSON matched exactly, including IDs, source levels, exclusions and rules. Twelve were refused (six each direction), representing133 repeated evaluations; one bearish plan was actionable at five boundaries. These are not138 distinct opportunities. There are fewer distinct setup identities than plan snapshots because a still-live setup can be replanned at another hour. The accessible older `to long.rtf` contained nine incomplete-run plans; all nine timestamp/direction/entry/stop tuples match the completed cohort. It corroborates the first nine only.

Planning as-of and actual5m decision boundary are distinct and both are recorded. Entry is the original1h planning close, not a claimed fill; execution would use the next actual5m market open under its gap/exposure/stop guards. All source confirmations in the catalog were known by planning as-of. No later target was selected with hindsight.

| Planning UTC | Direction | Entry | Stop | Risk/unit | Retained-before-R raw R values | Farther unique levels >=1R | Decision |
|---|---|---:|---:|---:|---|---:|---|
| 2024-01-03T10:00:00Z | bullish | 45344.67 | 45121.96 | 222.71 | 0.2974, 0.6975 | 2 | Correct under existing nearest-two policy; min R refused |
| 2024-01-04T22:00:00Z | bullish | 44430.86 | 43111.00 | 1319.86 | 0.0433, 0.1281 | 2 | Correct under existing nearest-two policy; min R refused |
| 2024-01-04T23:00:00Z | bullish | 44318.00 | 43192.17 | 1125.83 | 0.0106, 0.0355 | 3 | Correct under existing nearest-two policy; min R refused |
| 2024-01-08T07:00:00Z | bearish | 43912.87 | 44148.58 | 235.71 | 0.0225, 0.1367 | 179 | Correct under existing nearest-two policy; min R refused |
| 2024-01-08T08:00:00Z | bearish | 43974.55 | 44148.58 | 174.03 | 0.0045, 0.0549 | 189 | Correct under existing nearest-two policy; min R refused |
| 2024-01-10T21:00:00Z | bearish | 46127.00 | 46599.43 | 472.43 | 0.5236, 0.6922 | 254 | Correct under existing nearest-two policy; min R refused |
| 2024-01-10T22:00:00Z | bullish | 45946.00 | 44600.00 | 1346.00 | 0.1976, 0.2110 | 1 | Correct under existing nearest-two policy; min R refused |
| 2024-01-10T23:00:00Z | bullish | 46902.69 | 44600.00 | 2302.69 | 0.0126, 0.0312 | 0 | Correct under existing nearest-two policy; min R refused |
| 2024-01-15T23:00:00Z | bullish | 42628.00 | 42361.10 | 266.90 | 0.0785, 0.1190 | 169 | Correct under existing nearest-two policy; min R refused |
| 2024-01-22T14:00:00Z | bearish | 40943.17 | 41300.00 | 356.83 | 0.3984, 0.4012 | 28 | Correct under existing nearest-two policy; min R refused |
| 2024-01-22T15:00:00Z | bearish | 40694.75 | 41700.00 | 1005.25 | 0.0107, 0.0114 | 19 | Correct under existing nearest-two policy; min R refused |
| 2024-01-30T21:00:00Z | bearish | 43561.10 | 43720.96 | 159.86 | 0.0694, 0.2116 | 259 | Correct under existing nearest-two policy; min R refused |

The full diagnostic JSON contains every entry/invalidation/stop source, planning and actual boundary timestamps, setup identity, family, exact selected target prices/types/confirmation dates and versioned economic outcomes. The1277-row target CSV contains every eligible unique trade-side structural price in the thirteen catalogs: source IDs/types, observation/confirmation dates, age, available zone touch counts, cap status, raw R and normal/stress net R. Exact-price duplicates, unusable prices, wrong-side levels and future confirmations are excluded using the existing adapter. Unknown lifecycle validity remains unknown. Reproduction also saves the complete original rejection strings locally.

### Per-plan selected target evidence

**2024-01-03T10:00:00Z — bullish — failed_breakout_sweep_reversal**
Actual evaluated window: 2024-01-03T10:40:00+00:00 to 2024-01-03T10:40:00+00:00 (1 evaluations). Plan `492c5f7ff49dec2cac9a7f421a7ab460c6c64e647a25c14368acd59023b240fc`; setup `6c4157ce48036cd74ec6187ad692845e0a50a643fadc91c28ba87dca0286923e`.
Entry 45344.67000000; structural invalidation 45121.96000000; stop 45121.96000000 from planner_stop_buffer_none. Buffer: no buffer applied: stop equals the logical invalidation level. Risk is the exact positive entry–stop distance 222.71000000.
- Target 1: 45410.91000000, step4_reference_swing_high, source `823df1fbf597b3558753f804f9e2e2a462f89f69b8e6ae64337882b5e6752f76`, observed 2024-01-03T02:00:00+00:00, confirmed 2024-01-03T05:00:00+00:00; raw R 0.2974271474114319069642135513; net R normal -0.17408175, stress -0.42750500.
- Target 2: 45500.00000000, step4_reference_swing_high, source `796c7fcd3c57c695512e970ac854d7340e2c5722b352c78989ff7d2d1d51ab4d`, observed 2024-01-02T03:00:00+00:00, confirmed 2024-01-02T06:00:00+00:00; raw R 0.6974540882762336671007139329; net R normal 0.08056805, stress -0.25099149.
Reason: `minimum_r_multiple_not_met`; the two retained targets are both below the unchanged1R floor. Farther targets do not establish attainability across intervening barriers. No stop was squeezed, target invented or limit increased.

**2024-01-04T22:00:00Z — bullish — breakout_retest_continuation**
Actual evaluated window: 2024-01-04T22:00:00+00:00 to 2024-01-04T22:55:00+00:00 (12 evaluations). Plan `95d6a134dd5923b4ddb981221239cc28e25d2620576bc76613f57c7dbb38c93f`; setup `24d99f00b88246839784b951f1ec004be8f6f4235c921f8db7397cbc513c94de`.
Entry 44430.86000000; structural invalidation 43111.00000000; stop 43111.00000000 from planner_stop_buffer_none. Buffer: no buffer applied: stop equals the logical invalidation level. Risk is the exact positive entry–stop distance 1319.86000000.
- Target 1: 44488.00000000, step4_reference_swing_high, source `e2bc6d940d14185aacffac691003a3bfb93e1d0fdb4a95b806d87171779e0737`, observed 2023-12-05T22:00:00+00:00, confirmed 2023-12-06T01:00:00+00:00; raw R 0.04329247041352870759019896050; net R normal -0.04668988, stress -0.13731931.
- Target 2: 44600.00000000, step4_reference_zone, source `23bf7dea9b05cd541a2e7191ee26f3e35a7dcff2c2f66649498a8de27e9de8ec`, observed 2024-01-02T15:00:00+00:00, confirmed 2024-01-04T22:00:00+00:00; raw R 0.1281499552982892124922340248; net R normal 0.03084878, stress -0.06715210.
Reason: `minimum_r_multiple_not_met`; the two retained targets are both below the unchanged1R floor. Farther targets do not establish attainability across intervening barriers. No stop was squeezed, target invented or limit increased.

**2024-01-04T23:00:00Z — bullish — breakout_retest_continuation**
Actual evaluated window: 2024-01-04T23:00:00+00:00 to 2024-01-04T23:55:00+00:00 (12 evaluations). Plan `990212d5c796eb010aa74e448b05e6445413e5798aca7a36fed65935b1dca011`; setup `23075fb1e0537c54ebdee2cae1e59c8e5a620c4f48f4f5ec0760d4c33a9a6ed8`.
Entry 44318.00000000; structural invalidation 43192.17000000; stop 43192.17000000 from planner_stop_buffer_none. Buffer: no buffer applied: stop equals the logical invalidation level. Risk is the exact positive entry–stop distance 1125.83000000.
- Target 1: 44329.99000000, step4_reference_zone, source `36ac14092b9e79edc88ac9664ca969c44bbff609b535a9f92e016c81e0ce1b56`, observed 2024-01-04T15:00:00+00:00, confirmed 2024-01-04T23:00:00+00:00; raw R 0.01064992050309549399109989963; net R normal -0.08981268, stress -0.18811546.
- Target 2: 44358.02000000, step4_reference_swing_high, source `99490ddbd40cdf2a135c150a2d130e37382670691275ef9889a81d95aeb4cbba`, observed 2023-12-09T05:00:00+00:00, confirmed 2023-12-09T08:00:00+00:00; raw R 0.03554710746737962214543936474; net R normal -0.06739037, stress -0.16811482.
Reason: `minimum_r_multiple_not_met`; the two retained targets are both below the unchanged1R floor. Farther targets do not establish attainability across intervening barriers. No stop was squeezed, target invented or limit increased.

**2024-01-08T07:00:00Z — bearish — failed_breakout_sweep_reversal**
Actual evaluated window: 2024-01-08T07:00:00+00:00 to 2024-01-08T07:55:00+00:00 (12 evaluations). Plan `cb2dd20af135ec1a61124c646bc754299b1b8391fab269bc4bff317149fcf593`; setup `04a3f0a0bb588b1464e391da64c30bc8fe76fd8db39e08c3bc0f25c2b4f353b9`.
Entry 43912.87000000; structural invalidation 44148.58000000; stop 44148.58000000 from planner_stop_buffer_none. Buffer: no buffer applied: stop equals the logical invalidation level. Risk is the exact positive entry–stop distance 235.71000000.
- Target 1: 43907.57000000, step4_equal_level_cluster, source `12ca421474de93da1dc68c074972504715f7061ab373b6d837b43ec54134b8e5`, observed 2023-12-23T22:00:00+00:00, confirmed 2024-01-05T01:00:00+00:00; raw R 0.02248525730770862500530312672; net R normal -0.32771555, stress -0.52475491.
- Target 2: 43880.65000000, step4_equal_level_cluster, source `668c3472e3298ea08029fbe36716054d19dce7642bee460780e9643b4d65fa9d`, observed 2024-01-07T02:00:00+00:00, confirmed 2024-01-07T12:00:00+00:00; raw R 0.1366933944253531882397861779; net R normal -0.25262366, stress -0.47167164.
Reason: `minimum_r_multiple_not_met`; the two retained targets are both below the unchanged1R floor. Farther targets do not establish attainability across intervening barriers. No stop was squeezed, target invented or limit increased.

**2024-01-08T08:00:00Z — bearish — failed_breakout_sweep_reversal**
Actual evaluated window: 2024-01-08T08:00:00+00:00 to 2024-01-08T08:55:00+00:00 (12 evaluations). Plan `95c93c0fc381356620b3fbcb0a4e5f1abd0d567858deca861a04291e185583ad`; setup `04a3f0a0bb588b1464e391da64c30bc8fe76fd8db39e08c3bc0f25c2b4f353b9`.
Entry 43974.55000000; structural invalidation 44148.58000000; stop 44148.58000000 from planner_stop_buffer_none. Buffer: no buffer applied: stop equals the logical invalidation level. Risk is the exact positive entry–stop distance 174.03000000.
- Target 1: 43973.76000000, step4_reference_swing_high, source `1e848b412a8c76f9e0641d3767f7942e052df6fc94cfb6ea56334d88699cf9fa`, observed 2023-12-10T15:00:00+00:00, confirmed 2023-12-10T18:00:00+00:00; raw R 0.004539447221743377578578406022; net R normal -0.41135382, stress -0.60787659.
- Target 2: 43965.00000000, step4_reference_swing_high, source `5fc57306ea4cf2c1cb778170c31e458686765b510cdf5b16056b9c5c804733a1`, observed 2023-12-10T04:00:00+00:00, confirmed 2023-12-10T07:00:00+00:00; raw R 0.05487559616158133655116933862; net R normal -0.38185753, stress -0.58822781.
Reason: `minimum_r_multiple_not_met`; the two retained targets are both below the unchanged1R floor. Farther targets do not establish attainability across intervening barriers. No stop was squeezed, target invented or limit increased.

**2024-01-10T21:00:00Z — bearish — failed_breakout_sweep_reversal**
Actual evaluated window: 2024-01-10T21:00:00+00:00 to 2024-01-10T21:55:00+00:00 (12 evaluations). Plan `93961d1d665b8a37f8b00fce766ca9d48338c72d1640208a12778f1c7122edc8`; setup `dc83240f8306b2d9d55c5d6f1cadc3f6720d9080e258e79a768299bb8358f637`.
Entry 46127.00000000; structural invalidation 46599.43000000; stop 46599.43000000 from planner_stop_buffer_none. Buffer: no buffer applied: stop equals the logical invalidation level. Risk is the exact positive entry–stop distance 472.43000000.
- Target 1: 45879.63000000, step4_equal_level_cluster, source `7094aacb7b768b0f1e9b70a456e19ec52d7276a69b05bb60aec34aa1d180d02f`, observed 2024-01-02T09:00:00+00:00, confirmed 2024-01-02T17:00:00+00:00; raw R 0.5236119636771585208390661050; net R normal 0.19686404, stress -0.04981031.
- Target 2: 45800.00000000, step4_reference_swing_high, source `b4820eec2915299db879d1e482544497447bb8b3fb43c1104c63307e70dc38c9`, observed 2024-01-02T14:00:00+00:00, confirmed 2024-01-02T17:00:00+00:00; raw R 0.6921660351798149990474779332; net R normal 0.32927066, stress 0.05530723.
Reason: `minimum_r_multiple_not_met`; the two retained targets are both below the unchanged1R floor. Farther targets do not establish attainability across intervening barriers. No stop was squeezed, target invented or limit increased.

**2024-01-10T22:00:00Z — bullish — failed_breakout_sweep_reversal**
Actual evaluated window: 2024-01-10T22:00:00+00:00 to 2024-01-10T22:55:00+00:00 (12 evaluations). Plan `be9299b617acb7fbd3366f5d9cc5a486376b0d5b24234eacefafd2255a031398`; setup `82a2ac690464c0acc6075cfd15b1ea4ca0b9526f4f9d8109f417fa65fab63685`.
Entry 45946.00000000; structural invalidation 44600.00000000; stop 44600.00000000 from planner_stop_buffer_none. Buffer: no buffer applied: stop equals the logical invalidation level. Risk is the exact positive entry–stop distance 1346.00000000.
- Target 1: 46211.95000000, step4_reference_swing_high, source `6c2b9a0c68917786d65f5a21738ad05e4acacd917332c244bfa0097d743152e9`, observed 2024-01-10T03:00:00+00:00, confirmed 2024-01-10T06:00:00+00:00; raw R 0.1975854383358098068350668648; net R normal 0.09297409, stress -0.01213445.
- Target 2: 46230.00000000, step4_reference_swing_low, source `c9474e63eff2b79d5dfb2be344db9033c66a06592f91a10f74edbb906d2add72`, observed 2024-01-09T00:00:00+00:00, confirmed 2024-01-09T03:00:00+00:00; raw R 0.2109955423476968796433878158; net R normal 0.10521279, stress -0.00107271.
Reason: `minimum_r_multiple_not_met`; the two retained targets are both below the unchanged1R floor. Farther targets do not establish attainability across intervening barriers. No stop was squeezed, target invented or limit increased.

**2024-01-10T23:00:00Z — bullish — failed_breakout_sweep_reversal**
Actual evaluated window: 2024-01-10T23:00:00+00:00 to 2024-01-10T23:55:00+00:00 (12 evaluations). Plan `c866cf42d96b9da42ef17202793fde24134cfd08f71cb486a76d35e2790ff76d`; setup `82a2ac690464c0acc6075cfd15b1ea4ca0b9526f4f9d8109f417fa65fab63685`.
Entry 46902.69000000; structural invalidation 44600.00000000; stop 44600.00000000 from planner_stop_buffer_none. Buffer: no buffer applied: stop equals the logical invalidation level. Risk is the exact positive entry–stop distance 2302.69000000.
- Target 1: 46931.70000000, step4_equal_level_cluster, source `0fe16cbb493151c766eb5644cdbaeb5678b7d8500a184c5d9b1f496fc8069c63`, observed 2024-01-09T03:00:00+00:00, confirmed 2024-01-09T10:00:00+00:00; raw R 0.01259830893433332320025709062; net R normal -0.04210892, stress -0.10125319.
- Target 2: 46974.42000000, step4_reference_swing_high, source `dedcc22166814816f568f4fafccaa1d0ed56b4fecac83d487b86d7912d0c43f8`, observed 2024-01-09T03:00:00+00:00, confirmed 2024-01-09T06:00:00+00:00; raw R 0.03115052395242086429350021062; net R normal -0.02455902, stress -0.08478690.
Reason: `minimum_r_multiple_not_met`; the two retained targets are both below the unchanged1R floor. Farther targets do not establish attainability across intervening barriers. No stop was squeezed, target invented or limit increased.

**2024-01-15T23:00:00Z — bullish — failed_breakout_sweep_reversal**
Actual evaluated window: 2024-01-15T23:00:00+00:00 to 2024-01-15T23:55:00+00:00 (12 evaluations). Plan `da9e30e90e7cf559eda26a7b0a06a146630a59fd7f72eb46058216ae69fe4803`; setup `272c82f5a3e17731e6461fddd2208d4f589685f14af162b3cb2abac6fe4b0859`.
Entry 42628.00000000; structural invalidation 42361.10000000; stop 42361.10000000 from planner_stop_buffer_none. Buffer: no buffer applied: stop equals the logical invalidation level. Risk is the exact positive entry–stop distance 266.90000000.
- Target 1: 42648.95000000, step4_equal_level_cluster, source `98a97b3dd0e45f211d6227870af800f10c20b6f29e222db1d8a6698a823e4ee2`, observed 2023-12-14T00:00:00+00:00, confirmed 2023-12-14T06:00:00+00:00; raw R 0.07849381790932933683027351068; net R normal -0.25509611, stress -0.45894370.
- Target 2: 42659.75000000, step4_reference_swing_low, source `aa75e3a1f3ccc492b9905ac883e392c0ee8578aab2509fcebcd1ed752af35265`, observed 2023-12-14T03:00:00+00:00, confirmed 2023-12-14T06:00:00+00:00; raw R 0.1189584113900337204945672537; net R normal -0.22714765, stress -0.43864351.
Reason: `minimum_r_multiple_not_met`; the two retained targets are both below the unchanged1R floor. Farther targets do not establish attainability across intervening barriers. No stop was squeezed, target invented or limit increased.

**2024-01-22T14:00:00Z — bearish — breakout_retest_continuation**
Actual evaluated window: 2024-01-22T14:00:00+00:00 to 2024-01-22T14:55:00+00:00 (12 evaluations). Plan `1341b866c71b0d5842592d031ea6d4870e8e7d881b579297630a592edc2b583a`; setup `88ab06b508cb0ec761b1c1af257463d53be45aaf78f0978d32970a105cd89787`.
Entry 40943.17000000; structural invalidation 41300.00000000; stop 41300.00000000 from planner_stop_buffer_none. Buffer: no buffer applied: stop equals the logical invalidation level. Risk is the exact positive entry–stop distance 356.83000000.
- Target 1: 40801.00000000, step4_equal_level_cluster, source `5708453cf96b1d687418bcb6db775721f865bf6eb82b8ebe32a1152efa3928f7`, observed 2023-12-13T07:00:00+00:00, confirmed 2023-12-18T05:00:00+00:00; raw R 0.3984250203177983913908583920; net R normal 0.05874989, stress -0.18182316.
- Target 2: 40800.00000000, step4_reference_swing_low, source `168bf73557af622d1f7ed5248cf724d50228a7cf4e7ac58da07120cf30260b2d`, observed 2023-12-13T07:00:00+00:00, confirmed 2023-12-13T10:00:00+00:00; raw R 0.4012274752683350615138861643; net R normal 0.06087163, stress -0.18018353.
Reason: `minimum_r_multiple_not_met`; the two retained targets are both below the unchanged1R floor. Farther targets do not establish attainability across intervening barriers. No stop was squeezed, target invented or limit increased.

**2024-01-22T15:00:00Z — bearish — breakout_retest_continuation**
Actual evaluated window: 2024-01-22T15:00:00+00:00 to 2024-01-22T15:55:00+00:00 (12 evaluations). Plan `c6430daf90bf8d0f5deb2ecb30dfb638e3924aae462014f0f2eacf6b053b81ee`; setup `45f7d58affa945869c1cfdb1a18f7b4a436fde1605b19eca533530ea2c35c26b`.
Entry 40694.75000000; structural invalidation 41700.00000000; stop 41700.00000000 from planner_stop_buffer_none. Buffer: no buffer applied: stop equals the logical invalidation level. Risk is the exact positive entry–stop distance 1005.25000000.
- Target 1: 40684.00000000, step4_reference_swing_low, source `c6bdf08ecbcc51a896581b43c29fe494cfaf14a310d0c37e565cb69ed2306726`, observed 2024-01-22T05:00:00+00:00, confirmed 2024-01-22T08:00:00+00:00; raw R 0.01069385724944043770206416314; net R normal -0.09207564, stress -0.19158302.
- Target 2: 40683.28000000, step4_reference_swing_low, source `ac3c288dc439d8a5112a18adcfa6dea5a0cdecc77547393f72faadfb7f22eb2b`, observed 2024-01-18T20:00:00+00:00, confirmed 2024-01-18T23:00:00+00:00; raw R 0.01141009699079830887838846058; net R normal -0.09143223, stress -0.19101012.
Reason: `minimum_r_multiple_not_met`; the two retained targets are both below the unchanged1R floor. Farther targets do not establish attainability across intervening barriers. No stop was squeezed, target invented or limit increased.

**2024-01-30T21:00:00Z — bearish — failed_breakout_sweep_reversal**
Actual evaluated window: 2024-01-30T21:00:00+00:00 to 2024-01-30T21:55:00+00:00 (12 evaluations). Plan `5fafac8e00fc25c916b03e4f040aa4be9511fe6e04e5fa866c226b42127c3c74`; setup `1ca6eefc8a1c238e68d3d98fbeffcb9994608b75e599e1c73afd82438afc1d63`.
Entry 43561.10000000; structural invalidation 43720.96000000; stop 43720.96000000 from planner_stop_buffer_none. Buffer: no buffer applied: stop equals the logical invalidation level. Risk is the exact positive entry–stop distance 159.86000000.
- Target 1: 43550.00000000, step4_equal_level_cluster, source `3bb76a14daab09ab0395c5fa1909094263052a17468fad67472a0bb6d74c99ea`, observed 2023-12-08T04:00:00+00:00, confirmed 2023-12-22T00:00:00+00:00; raw R 0.06943575628675090704366320530; net R normal -0.39302815, stress -0.60158803.
- Target 2: 43527.27000000, step4_reference_swing_low, source `77ada55947429a1fcbe2edfc68b6b980cc424093d35a68502b3f5a2ac7bd4c6c`, observed 2024-01-06T12:00:00+00:00, confirmed 2024-01-06T15:00:00+00:00; raw R 0.2116226698361065932691104717; net R normal -0.31232817, stress -0.54861713.
Reason: `minimum_r_multiple_not_met`; the two retained targets are both below the unchanged1R floor. Farther targets do not establish attainability across intervening barriers. No stop was squeezed, target invented or limit increased.

## Target selection and structural quality findings

Eleven of twelve refusals have farther structural levels mathematically above1R; this agrees with eight of nine in the older sample. The exception is January10 23:00: no available genuine level reaches1R even without the cap. Proximity sorting is correct for the current documented policy. The cap operates BEFORE the1R test; this is verified and deliberately retained. Its conservatism is not proof of optimality, but neither is a distant price proof of a missed viable trade.

For January3, the45,800 level is about2.04 raw R, but is only about0.938 net-to-net R under the declared normal costs, even BEFORE considering the nearby45,410.91/45,500 obstacles. No hypothetical fill/exit or successful outcome is inferred from that level.

The upstream zone algorithm does use measured touches and recency when bounding its zone set (last40 swings, minimum1touch, maximum12zones by default). The planner additionally adapts ALL confirmed swing points in its stored history, the active range and optional equal clusters; it does not have a combined level-quality score. Exact-price duplicates are collapsed; nearby distinct prices/overlapping structures are NOT silently declared identical or independent confirmations. Old support can become resistance and vice versa, so age or a past crossing alone is not a valid exclusion. Zone `known_at` is the current reconstruction time while its members may be older; member observation dates are exposed rather than calling it newly strong.

There is no target-adapter lifecycle field proving that every old reference remains unbroken/active. The CSV marks that fact UNKNOWN. No measured rule distinguishes a minor pivot from historically important resistance across all conditions, and no calibrated probability says a target will be reached. These are missing modelling/evidence capabilities, not a reproduced algorithmic reason to discard levels or bypass nearby obstacles. A new quality score was therefore NOT invented from these twelve losing/refused examples.

## Stop, timing, identity and accounting audit

Existing family-specific tests and actual reconstructed plans verify structural invalidation, NONE/ATR/percentage buffers, correct long/short side, positive risk, causal source dates, family confirmations, range bounds and expired/invalid qualification refusals. Current forward/historical cost-adjusted observational R already has correct directional arithmetic. It divides by ORIGINAL price risk for reporting; the new economic gate instead uses net STOP LOSS including costs. The two metrics must not be conflated.

No confirmed need was found to tighten/widen stops or change sizing. Simulator sizing still uses risk budget, adverse entry ceiling, round-trip costs, cash, quantity step and maximum exposure, and checks minimum notional again at the actual fill. Gap-stop execution remains adverse, simultaneous stop/target remains stop-first, missing data remains unresolved and frozen. Default assumptions are0.5%risk,25%notional cap, one cash-Spot long position. They are research assumptions, not exact historical venue fee/lot tiers. Price precision is Decimal; a separate precision regression prevents rounded cost ratios from admitting subfloor economics.

Plan identity includes setup identity, as-of, source facts, parameters and rules version. The simulator deduplicates exact source plan IDs, not similar prices. A genuinely different identity is not suppressed. A replan at a later hour can be a new plan even for the same setup: no unapproved setup-wide lifetime trade limit was introduced. Missing/duplicate candles, future additions, wrong timeframes, expiry and restart controls remain in the original suite; new lifecycle SQLite tests cover pending/spent IDs after reopening. The known shallow-history strictxfail is preserved and remains a real limitation.

## Bounded before/after comparison

This replays DOWNSTREAM changes against the completed frozen hierarchy observations; it is NOT another full hierarchy computation or an independent new out-of-sample experiment. All thirteen real planner outputs were reconstructed first. Each wallet processes the same8929 inclusive closed boundaries (Jan1 00:00 throughFeb1 00:00, including the Dec31 23:55 opening bar:744h5m). Four variants ×three policies ×two cost scenarios =24wallets and214296 correlated wallet-boundary evaluations.

| Variant | Boundaries per wallet | Executable Spot plans | Signals | Fills | Closed trades | Net PnL | Additional trades |
|---|---:|---:|---:|---:|---:|---:|---:|
| Original unchanged |8929|0|0|0|0|0|0|
| Lifecycle repair only |8929|0|0|0|0|0|0|
| Economic mode only |8929|0|0|0|0|0|0|0|
| Combined |8929|0|0|0|0|0|0|0|

All rows above hold for A1/A2/A3 and normal/stress costs. PF, win/loss averages, holding duration and cost-adjusted trade expectancy are undefined (no trade sample), not0. Max drawdown is0 for inactive wallets; this does not demonstrate risk robustness. Unresolved/ambiguous events0.

Original A1/A2/A3 considered4/9/13 distinct plan snapshots at30/90/138 plannable-gate evaluations respectively. Their unchanged raw planner refused25/85/133 evaluations. Each saw five evaluations of ONE actionable bearish plan. The economic mode adds five economic refusals and leaves the raw refusals unchanged. No actionable cash-Spot long was suppressed by simulator dedup in this month because no long signal ever reached execution. The lifecycle fault is real in regression controls, but it is NOT the cause of January zero trades.

The bearish January19 plan had entry41,239.57, stop41,280, first target41,146.83: raw2.29384R. Normal-cost net potential reward is−22.600922904 perBTC versus net stop loss155.957414172 (netR−0.14491727); stress netR−0.54907244. Target2 is also negative net reward. These are hypothetical per-unit level calculations, NOT a simulated short fill or realised profit. Thus adding a short model would not automatically make this particular plan viable. No short execution model was fabricated.

Normal costs:10bp fee each side,2bp full spread and3bp adverse slippage each side (4bp total adverse impact per fill). Stress:20bp fee,4bp full spread,9bp slippage (11bp impact per fill). Entries would be next-open, never the planning-close number substituted as a fill. The source candle fingerprints matched original official archive receipts; observations SHA256 before/after matches exactly in receipt.json. Old databases were opened mode=ro; all new checkpoints/events are in a different marked lab.

## Tests, reproduction and delivery

New tests cover occupied-slot/rejected fills, entry gaps, minimum notional, capital, missing candles, reopening SQLite, spent-plan suppression, new IDs, exact long/short fees/fills, worsening costs, rounded1R boundary, planner version/identity, zero costs, existing-v2 fingerprint, historical/forward scenario binding, explicit refusal explanations and catalog cap/duplicate/future facts. Synthetic fixtures are TEST-ONLY and never substituted for historical performance. Initial six lifecycle controls failed on76. Current46 independently passed43 tests; its rounded-threshold counterexample is separately recorded. An initial cap test incorrectly used a fixture with only one target; the corrected test supplies three explicit synthetic prices plus a duplicate and future fact and preserves stronger cap assertions.

```bash
python -m pytest tests/test_plan_lifecycle_audit.py tests/test_trading_economics_audit.py tests/test_logic_audit_contract.py
python -m pytest
node --test tests/frontend/*.test.mjs
# From this source checkout with the original completed LAB-ONLY January cohort:
# If shallow-cloned, first fetch the pinned public76 source object:
git fetch origin 2adb4ee243b45d10d43df3acb61be193c86dc308
python -m trading_assistant.strategy_lab.logic_audit \
  --source /absolute/path/to/marked/hierarchy-repaired \
  --output /absolute/path/to/a/NEW/marked/logic-audit-results
```

The audit command has no downloader, account, order or holdout path. It requires the original completed cohort, validates candle fingerprints and exact plan equality, preserves the two-target rule, and loads only the SHA256-verified pinned original simulator from Git for the baseline. Identical experiment definitions resume/deduplicate immutable checkpoints. Source and destination must differ. It cannot reconstruct the original full comparison from candles alone without that saved cohort; the genuine market records/receipts were preserved locally, not manufactured.

To opt into the economic mode, pass a `PlanningParameters(economic_costs=FrictionAssumptions(...))` object. Historical/forward services override its costs with THEIR recorded declared scenario before planning. None deliberately remains legacy/unverified. Config fingerprints and the returned plan rules-version distinguish these regimes; users must not mix them as identical strategies. No existing records are migrated.

## Remaining limits and best next task

BRAIN still has no demonstrated cost-adjusted advantage. The target cap is conservative and not proven optimal; structural reference strength, lifecycle and intervening obstacles remain insufficiently modelled. Old references cannot be declared valid or invalid from age alone. Zero trade samples cannot evaluate profits. Cash Spot still does not implement shorts; continuous live-market paper reliability is not certified by this audit.2024was already examined; only the requested fixed December2023/January2024 source is used here, never untouchedOOS.2025remains sealed and2026is not used. No new strategy/family/threshold search was conducted.

The single best next engineering task is a causal structural-reference lifecycle/obstacle evidence contract with independently labelled replay fixtures BEFORE proposing any target-ranking change. It should establish which nearer levels remain meaningful barriers; it must not optimise this twelve-plan sample or simply unlock farther targets. Human review/manual merge is required for this draft. PR46 remains separate: this optional compatibility-preserving gate is an alternative selective adaptation, not a declaration that all of46 was merged. Reconcile overlap explicitly at review.
