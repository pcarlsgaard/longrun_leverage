# Nasdaq price-of-convexity screen

The purpose is to compare owning versus selling upside at actual economic cost, not to optimize a giant historical grid. The existing outright simulation remains the anchor; an exact arithmetic parity test pins the zero-overwrite case against it.

## Stage 1: prespecified screen

- Long strikes 80%, 85%, 90%; short strikes 120%, 135%, 150%; overwrite 0%, 50%, 100%. This is 21 distinct option structures.
- Fixed net debit budgets 30% and 40%, plus target initial delta 1.0x and 1.25x **at each roll**, with a hard 50% net debit cap; cap-hit frequency and actual entry delta are reported.
- Bills and daily 50/50 bills/long bonds reserves.
- Same approximately two-year listed maturity and annual roll as original. Both legs have same expiration. No naked short, borrowing or margin-dependent leverage. Fractional contracts for research. Actual broker margin and assignment handling remain unmodeled.
- Historical joint bootstrap plus favorable, normalization, AI with expensive capital and fiscal-stress transforms, using identical draws across comparisons.
- Baseline lagged realized vol +3 points, persistent +9 points, persistent +9 points with 20% upper-call time-value credit haircut, and initial +9 points decaying to +3 over one year with the same haircut. The haircut is a bounded pairwise credit stress, **not** a calibrated or globally arbitrage-free IV surface.
- Both option legs separately incur 100bp mid-to-fill costs; roll sales include short buyback. Final wealth marked at mid, consistent with the original comparison. European pricing and no taxation.
- 128 paired 20-year paths per scenario for screening only. The aggregate recomputes quantiles from raw paths rather than averaging shard statistics.

Outputs: summary.csv (median and fifth percentile real CAGR, P(drawdown >60%), P(beat Nasdaq 1x), actual delta, cap frequency, Pareto flag, frequency credit bound binds); paired.csv (same-strike, same sizing policy, same reserve paired incremental CAGR and drawdown vs outright); paths.csv.

Pareto means nondominated in median CAGR, fifth percentile CAGR and probability of 60% drawdown; it is not a forecast of optimal allocation. Fixed budget and target delta families both shown; target delta can be infeasible under cap, so inspect actual delta and cap frequency before calling comparisons matched.

## Stage 2: confirm rather than optimize

Preselect a small set of structurally stable candidates across neighboring strikes, overwrite and sizing (including outright and NDX 1x). Run independent seeds with at least 2,048 paired paths per scenario, include +6pp and +12pp pricing, varied short credits, 6/12/18-month rolls and different Treasury duration. Add 25%/75% overwrite only around stable families rather than mining all combinations. Distinguish transient entry pricing from persistent pricing. Compare also matched delta with a same-risk reserve, and unleveraged QQQ mixed with bills. Test an adaptive rule only after static comparisons: use contemporaneous long ask and upper bid, compare prospective growth and loss constraints, do not buy a spread merely because IV is high. Use a conservative switching hurdle; no future realized volatility in decisions. Existing price-only and secular regime strategies remain benchmarks.

## Before deploying

Obtain synchronized executable broker quotes and whole-contract feasibility; compare QQQ American-style and index European-style contracts, expiration/assignment and tax treatment. The pricing surface and paths are hypothetical. Selection must be contingent on validated data and a stated drawdown budget. This experiment is not a trade signal.
