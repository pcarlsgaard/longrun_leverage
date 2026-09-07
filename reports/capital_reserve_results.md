# Capital reserve / dry-powder experiment

Frozen sample: **1986-09-26–2026-09-02**. Original daily histories, funding, fees, T-bill yield accrual, TR-SMA construction and common falsification dates are reused unchanged.

## Main comparison

| series | lag | cost_bps | cagr | max_drawdown | terminal_multiple | average_reserve_weight | average_effective_equity_exposure |
| --- | --- | --- | --- | --- | --- | --- | --- |
| NO_RESERVE | LAG1 | 0 | 19.59% | -74.49% | 1266.7687 | 0.00% | 2.5640 |
| FIXED_90_10 | LAG1 | 0 | 18.55% | -68.51% | 894.7846 | 9.85% | 2.3156 |
| FIXED_85_15 | LAG1 | 0 | 17.98% | -65.15% | 736.6465 | 14.78% | 2.1910 |
| HWM_RESERVE | LAG1 | 0 | 19.52% | -74.31% | 1237.5309 | 1.33% | 2.5285 |
| ASYMMETRIC_RESERVE_BAND | LAG1 | 0 | 19.20% | -73.77% | 1112.1419 | 3.80% | 2.4623 |
| NO_RESERVE | LAG1 | 25 | 17.88% | -79.48% | 712.3039 | 0.00% | 2.5640 |
| FIXED_90_10 | LAG1 | 25 | 17.03% | -74.07% | 533.6389 | 9.85% | 2.3156 |
| FIXED_85_15 | LAG1 | 25 | 16.55% | -70.97% | 452.3424 | 14.78% | 2.1910 |
| HWM_RESERVE | LAG1 | 25 | 17.86% | -79.34% | 707.1841 | 1.21% | 2.5320 |
| ASYMMETRIC_RESERVE_BAND | LAG1 | 25 | 17.51% | -78.89% | 628.0842 | 3.80% | 2.4623 |
| NO_RESERVE | LAG2 | 0 | 16.82% | -78.24% | 496.5337 | 0.00% | 2.5640 |
| FIXED_90_10 | LAG2 | 0 | 16.36% | -71.12% | 424.1279 | 9.98% | 2.3133 |
| FIXED_85_15 | LAG2 | 0 | 16.02% | -67.51% | 377.4377 | 14.95% | 2.1879 |
| HWM_RESERVE | LAG2 | 0 | 16.89% | -76.34% | 508.6846 | 1.27% | 2.5311 |
| ASYMMETRIC_RESERVE_BAND | LAG2 | 0 | 16.95% | -71.89% | 520.1531 | 3.40% | 2.4768 |
| NO_RESERVE | LAG2 | 25 | 15.15% | -78.28% | 279.2008 | 0.00% | 2.5640 |
| FIXED_90_10 | LAG2 | 25 | 14.86% | -71.12% | 253.0352 | 9.98% | 2.3133 |
| FIXED_85_15 | LAG2 | 25 | 14.61% | -67.51% | 231.8777 | 14.95% | 2.1879 |
| HWM_RESERVE | LAG2 | 25 | 15.26% | -76.36% | 289.9786 | 1.15% | 2.5346 |
| ASYMMETRIC_RESERVE_BAND | LAG2 | 25 | 15.29% | -74.38% | 293.7996 | 3.40% | 2.4768 |

NO_RESERVE means `UPRO_SMA_TO_SP500` or `SSO_SMA_TO_SP500`, as identified by the fund column. `FIXED_90_10` and `FIXED_85_15` are aliases for `STATIC_RESERVE_10` and `STATIC_RESERVE_15`. HWM_RESERVE and ASYMMETRIC_RESERVE_BAND use staged recovery deployment. HWM_DRAWDOWN and BAND_DRAWDOWN are separate deployment sensitivities, never combined with staging.

## Equal-average-reserve and exposure controls

| series | average_reserve | rounded_fixed_reserve | dynamic_cagr | fixed_cagr | dynamic_to_fixed_terminal_ratio | dynamic_average_effective_exposure | constant_leverage_cagr |
| --- | --- | --- | --- | --- | --- | --- | --- |
| HWM_RESERVE | 0.0115 | 0.0000 | 0.1526 | 0.1515 | 1.0386 | 2.5346 | 0.1441 |
| ASYMMETRIC_RESERVE_BAND | 0.0340 | 0.0500 | 0.1529 | 0.1504 | 1.0903 | 2.4768 | 0.1443 |
| HWM_DRAWDOWN | 0.0672 | 0.0500 | 0.1523 | 0.1504 | 1.0660 | 2.4002 | 0.1444 |
| BAND_DRAWDOWN | 0.0727 | 0.0500 | 0.1516 | 0.1504 | 1.0418 | 2.3848 | 0.1444 |

Matching uses the full-sample mean reserve, rounded half-up to 0/5/10/15/20% etc. This is an ex-post diagnostic, not a deployable forecast or parameter optimization. The fixed target and its realized mean differ through drift. Equal cash allocation is not equal risk: state-dependent exposure, volatility and drawdowns remain different. Constant leverage uses the dynamic policy’s mean effective exposure with the original inferred funding+spread and the same fund fee; no core return history is rebuilt. Its daily reset is synthetic and incurs no additional investor state-switch fee.

## Assessment: no reserve is the best-supported growth architecture

**The complex reserve rules do not establish robust full-cycle value.** Keep the no-reserve SMA rule as the parsimonious growth baseline. A fixed reserve is an interpretable choice when deliberately accepting lower exposure and smaller drawdowns, but that is a different objective from demonstrating superior geometric wealth. Staging is a deployment mechanism, not a separately funded reserve architecture.

At **UPRO / LAG2 / 25 bp**, HWM CAGR is **15.26%** and band CAGR **15.29%**, versus **15.15%** without reserve: improvements of only **0.11 / 0.15 percentage points/year**. The favorable headline does not persist across execution assumptions, the lower-leverage sleeve and distinct historical cycles.

1. **Does a reserve improve long-run wealth? Only conditionally.** UPRO LAG2/25 bp terminal wealth rises 3.9% with HWM and 5.2% with the band. For SSO under the same assumptions, CAGR is 12.96% / 12.65%, versus 13.00% without reserve. LAG1/zero-cost dynamic reserve rules also fail to improve on no reserve.

2. **Is it more than fixed cash? Some UPRO timing value appears, but it is not robust.** The exact-average-target diagnostic below supplements the prespecified 5-point rounding. Exact matching sets the fixed target to the observed dynamic mean, without fitting returns; drift means realized average weights remain approximate. Neither comparison is exact risk matching.

| series | dynamic_cagr | exact_average_fixed_cagr | dynamic_to_exact_average_fixed_terminal_ratio |
| --- | --- | --- | --- |
| HWM_RESERVE | 0.1526 | 0.1513 | 1.0444 |
| ASYMMETRIC_RESERVE_BAND | 0.1529 | 0.1509 | 1.0746 |
| HWM_DRAWDOWN | 0.1523 | 0.1499 | 1.0861 |
| BAND_DRAWDOWN | 0.1516 | 0.1497 | 1.0686 |

3. **Does HWM retain bull upside? Yes, mostly because it holds very little reserve.** It averages only 1.15% bills, versus 9.98% for fixed 90/10. The gain cap is rarely binding, and long underwater periods prevent new harvesting. HWM therefore preserves upside at the cost of often lacking meaningful dry powder.

4. **Are recoveries shorter? Mainly in 1987.** The own-HWM recovery dates improve sharply there, but by only days to weeks in most later episodes. Both staged architectures have zero bills at the 2002 trough, and HWM also has zero at the 2009 trough. A wealth ratio above one in those episodes can simply carry forward an earlier gain.

5. **Is staging better than drawdown buying? No stable dominance.** UPRO LAG2/25 bp staged versus drawdown HWM CAGR is 15.26% versus 15.23%; band is 15.29% versus 15.16%. SSO band drawdown deployment beats its staged version, while both lag no reserve. Hold-only controls confirm that leaving accumulated cash unused is costly; that does not prove the particular deployment timing is optimal.

6. **Does it reconstruct risk capacity? When cash survives, yes; reliably across crashes, no.** Dry-powder ratios and marked deployed-lot wealth quantify the capacity, with adverse-state deployment limited to 1×. Repeated favorable episodes can spend the reserve before the eventual trough. The large 1987 band effect also partly comes from its initial 10% reserve, whereas HWM starts with zero.

7. **What bull CAGR is sacrificed?** The table summarizes positive-return favorable episodes lasting at least 60 sessions. Medians are descriptive, not an estimate of a permanent annual penalty. Zero median loss for a dynamic rule often means the reserve was empty; occasional relative gains within favorable episodes reflect daily compounding and lower exposure. The CSV retains every eligible episode and links it to the next named stress cycle. Those repeated cycle references must not be summed.

| series | episodes | median_no_reserve_cagr | median_reserve_cagr | median_cagr_sacrifice_pp | cumulative_log_growth_foregone |
| --- | --- | --- | --- | --- | --- |
| NO_RESERVE | 24 | 0.3954 | 0.3954 | 0.0000 | 0.0000 |
| FIXED_90_10 | 24 | 0.3954 | 0.3620 | 2.9341 | 0.7069 |
| FIXED_85_15 | 24 | 0.3954 | 0.3452 | 4.4362 | 1.0722 |
| HWM_RESERVE | 24 | 0.3954 | 0.3954 | 0.0000 | 0.0315 |
| ASYMMETRIC_RESERVE_BAND | 24 | 0.3954 | 0.4005 | 0.0000 | 0.1278 |
| HWM_DRAWDOWN | 24 | 0.3954 | 0.3707 | 1.3485 | 0.4424 |
| BAND_DRAWDOWN | 24 | 0.3954 | 0.3781 | 1.7506 | 0.4988 |

8. **Does recovery repay the cost? Generally not in the later staged-reserve cycles.** The following local-cycle accounting equalizes starting wealth at the previous named crash’s underlying recovery, then includes all accumulation up to the next peak. The first cycle starts at sample entry. It removes inherited dollar advantages without resetting reserve policy state. At three years, HWM repays the 1987 shortfall but not the 2000, 2020 or 2022 shortfalls; 2008 has essentially no local reserve activity. The band also fails to repay 2000, 2020 and 2022. Near-zero costs and negative costs have undefined payback ratios.

| series | event | cycle_local_recovery_multiplier | cycle_local_reserve_payback_ratio | dry_powder_ratio | reserve_recovery_days | no_reserve_recovery_days |
| --- | --- | --- | --- | --- | --- | --- |
| HWM_RESERVE | 1987 | 1.0677 | 14.0222 | 0.1137 | 2817 | 2878 |
| HWM_RESERVE | 2000_2002 | 0.9825 | 0.0000 | 0.0000 | 3871 | 3873 |
| HWM_RESERVE | 2007_2009 | 1.0000 | nan | 0.0000 | 1528 | 1530 |
| HWM_RESERVE | 2020 | 0.9872 | -0.3679 | 0.0083 | 156 | 156 |
| HWM_RESERVE | 2022 | 0.9960 | 0.8725 | 0.0143 | 615 | 630 |
| ASYMMETRIC_RESERVE_BAND | 1987 | 1.1911 | 5.3756 | 0.4037 | 2293 | 2878 |
| ASYMMETRIC_RESERVE_BAND | 2000_2002 | 0.9283 | 0.0000 | 0.0000 | 3870 | 3873 |
| ASYMMETRIC_RESERVE_BAND | 2007_2009 | 1.0240 | nan | 0.0066 | 1527 | 1530 |
| ASYMMETRIC_RESERVE_BAND | 2020 | 0.9386 | 0.0166 | 0.0775 | 154 | 156 |
| ASYMMETRIC_RESERVE_BAND | 2022 | 0.9634 | 0.4541 | 0.0208 | 614 | 630 |

9. **Are results consistent across crises? No.** 1987 supplies much of the surviving staged reserve advantage. The 2010-onward comparison loses relative wealth for both staged rules:

| series | period | terminal_ratio_vs_no_reserve |
| --- | --- | --- |
| HWM_RESERVE | 1987_1999 | 1.0427 |
| HWM_RESERVE | 2000_2009 | 1.0009 |
| HWM_RESERVE | 2010_2019 | 0.9911 |
| HWM_RESERVE | 2020_latest | 0.9990 |
| HWM_RESERVE | 2010_latest | 0.9900 |
| ASYMMETRIC_RESERVE_BAND | 1987_1999 | 1.0850 |
| ASYMMETRIC_RESERVE_BAND | 2000_2009 | 1.0277 |
| ASYMMETRIC_RESERVE_BAND | 2010_2019 | 0.9495 |
| ASYMMETRIC_RESERVE_BAND | 2020_latest | 0.9788 |
| ASYMMETRIC_RESERVE_BAND | 2010_latest | 0.9294 |

10. **Do gains survive execution delay and costs? The small UPRO LAG2 gain does survive 10–25 bp, but it is not invariant to lag or leverage.** The complete grid includes 50 bp and reports both direct charged-cost drag and endogenous changes in policy decisions.

11. **Does complexity beat simple controls materially? Not with sufficient robustness.** The prespecified HWM cap/harvest grid has the following LAG2/25-bp CAGR ranges; similar cap results mostly mean caps are inactive, rather than demonstrating a universal robust optimum.

| fund | min | max |
| --- | --- | --- |
| SSO | 0.1294 | 0.1298 |
| UPRO | 0.1520 | 0.1530 |

12. **Conclusion: the added complexity is not justified by this experiment.** No reserve is the best-supported baseline for the stated geometric-growth objective. Fixed reserve is the clearer alternative for an explicit capital-preservation objective. Do not market either as free dry powder, and do not infer future performance from five overlapping historical crisis narratives.

## Fresh-investor rolling cohorts

These monthly cohorts start with fresh $1, each rule’s prescribed initial reserve, new HWM and confirmation state. Underlying SMA/drawdown history remains observable before entry. They check whether the inherited-policy results depend on a reserve built or spent before the investor starts. Both funds/lags and 0/25 bp are included in the CSV; the important LAG2/25-bp results follow. Cohorts overlap extensively. Use the median of paired wealth ratios, not the ratio of separately calculated medians. Drawdown deployment improves some 20-year outcomes, but its UPRO 30-year paired median is approximately break-even at LAG2/25 bp and lower under the other lag/cost combinations.

| series | fund | horizon_years | cohorts | min_cagr | median_cagr | min_terminal_multiple | median_terminal_multiple | median_ratio_vs_no_reserve |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ASYMMETRIC_RESERVE_BAND | SSO | 20 | 240 | 0.0475 | 0.1001 | 2.5294 | 6.7417 | 0.9295 |
| ASYMMETRIC_RESERVE_BAND | SSO | 30 | 120 | 0.0964 | 0.1212 | 15.8246 | 30.9407 | 0.8574 |
| ASYMMETRIC_RESERVE_BAND | UPRO | 20 | 240 | 0.0568 | 0.1243 | 3.0210 | 10.4243 | 0.9683 |
| ASYMMETRIC_RESERVE_BAND | UPRO | 30 | 120 | 0.1100 | 0.1557 | 22.8933 | 76.7606 | 0.8905 |
| BAND_DRAWDOWN | SSO | 20 | 240 | 0.0528 | 0.1045 | 2.8010 | 7.2975 | 1.0071 |
| BAND_DRAWDOWN | SSO | 30 | 120 | 0.0982 | 0.1242 | 16.6329 | 33.5115 | 0.9326 |
| BAND_DRAWDOWN | UPRO | 20 | 240 | 0.0683 | 0.1288 | 3.7518 | 11.2879 | 1.0600 |
| BAND_DRAWDOWN | UPRO | 30 | 120 | 0.1119 | 0.1604 | 24.1206 | 86.8501 | 0.9994 |
| FIXED_85_15 | SSO | 20 | 240 | 0.0476 | 0.0976 | 2.5334 | 6.4406 | 0.8917 |
| FIXED_85_15 | SSO | 30 | 120 | 0.0925 | 0.1159 | 14.2220 | 26.8400 | 0.7461 |
| FIXED_85_15 | UPRO | 20 | 240 | 0.0597 | 0.1213 | 3.1919 | 9.8751 | 0.9051 |
| FIXED_85_15 | UPRO | 30 | 120 | 0.1064 | 0.1484 | 20.7480 | 63.5246 | 0.7423 |
| FIXED_90_10 | SSO | 20 | 240 | 0.0479 | 0.1000 | 2.5504 | 6.7251 | 0.9296 |
| FIXED_90_10 | SSO | 30 | 120 | 0.0943 | 0.1198 | 14.9390 | 29.7981 | 0.8270 |
| FIXED_90_10 | UPRO | 20 | 240 | 0.0594 | 0.1225 | 3.1709 | 10.0783 | 0.9416 |
| FIXED_90_10 | UPRO | 30 | 120 | 0.1065 | 0.1526 | 20.8040 | 70.8588 | 0.8284 |
| HWM_DRAWDOWN | SSO | 20 | 240 | 0.0481 | 0.1048 | 2.5572 | 7.3430 | 1.0048 |
| HWM_DRAWDOWN | SSO | 30 | 120 | 0.0972 | 0.1258 | 16.1480 | 35.0037 | 0.9780 |
| HWM_DRAWDOWN | UPRO | 20 | 240 | 0.0583 | 0.1269 | 3.1039 | 10.9095 | 1.0346 |
| HWM_DRAWDOWN | UPRO | 30 | 120 | 0.1062 | 0.1601 | 20.6316 | 86.1103 | 0.9968 |
| HWM_RESERVE | SSO | 20 | 240 | 0.0477 | 0.1034 | 2.5417 | 7.1591 | 0.9858 |
| HWM_RESERVE | SSO | 30 | 120 | 0.0962 | 0.1259 | 15.7458 | 35.0471 | 0.9719 |
| HWM_RESERVE | UPRO | 20 | 240 | 0.0575 | 0.1240 | 3.0604 | 10.3540 | 0.9867 |
| HWM_RESERVE | UPRO | 30 | 120 | 0.1034 | 0.1590 | 19.1278 | 83.5814 | 0.9721 |
| NO_RESERVE | SSO | 20 | 240 | 0.0481 | 0.1042 | 2.5585 | 7.2599 | 1.0000 |
| NO_RESERVE | SSO | 30 | 120 | 0.0972 | 0.1270 | 16.1633 | 36.0829 | 1.0000 |
| NO_RESERVE | UPRO | 20 | 240 | 0.0577 | 0.1242 | 3.0687 | 10.3930 | 1.0000 |
| NO_RESERVE | UPRO | 30 | 120 | 0.1044 | 0.1598 | 19.6536 | 85.4121 | 1.0000 |

## Accounting and implementation conventions

- All policies start with $1. HWM starts with no bills, fixed policies start at their target, and the band starts with 10% bills. Initial allocations are not charged as transitions, matching the existing framework. There are no contributions, withdrawals, taxes or external reserve funding.

- A new total-portfolio high advances the HWM immediately when recognized, even when the cap blocks a transfer. Gain-harvest orders use only the lag-eligible observed close, are executed within current available capital, and are capped using execution-close total wealth. The cap limits accumulation; passive reserve weight can exceed it after risky losses.

- The band’s 20% upper boundary is a drift/reference boundary, not a mandatory crash sale of bills: no forced buying of leveraged equities below SMA. Above 5%, no ordinary replenishment occurs. Below 5%, favorable-state rebuilding targets 10%. After deployment, replenishment requires a newly observed all-history high in the unreserved risky sleeve, before investor switching charges, after that deployment. At most one reserve transfer occurs per execution close; deployment takes precedence.

- Staging starts on an executed unfavorable→favorable transition, not an initially favorable sample entry. First tranche is one-third of current bill units; second is another third of episode-entry units after 20 completed favorable sessions; all remaining bills, including new harvests, follow 60 completed sessions. Interest stays with each tranche. A new episode re-splits remaining bills and resets confirmation. A continuing adverse signal never causes a staged purchase.

- Drawdown uses the existing 1× S&P total-return index from its all-history high, lagged like the SMA. The first threshold snapshots bill units; 20/25/25/30% are deployed once per underlying drawdown episode. Gapped thresholds execute together. Thresholds reset only after the underlying regains its high. Additional later reserve accumulation is not part of the original snapshotted pool. All adverse-state buys follow 1×, favorable buys follow the fund.

- LAG1 orders from close t affect return ending t+1; LAG2 affects t+2. HWM/band observations and drawdown triggers use the same lag. Fixed calendar rebalances receive the same extra session delay. Staging counts executed allocation states.

- Costs use half-L1 turnover across actual old/new risky assets and bills. A full risky switch plus reserve deployment charges the union of those trades once, not their sum. Costs are paid proportionally from post-transfer holdings. Category trade counts may overlap; total charged transition days and net turnover do not. Direct cost drag holds the realized decision path fixed; zero-cost-versus-cost total drag also includes changes in future decisions.

- Exposure uses beginning-of-return, post-trade weights. Fraction at exactly 1×/2×/3× means total portfolio exposure, with separate sleeve-state fractions. A partly reserved 3× sleeve generally gives less than 3× total exposure.

- Stress dates use a common underlying trough and its preceding all-history high. All wealth is measured per original $1, including accumulation before the crash. Deployed-lot marking removes subsequent proportional harvesting and includes later costs; the cash-only alternative is gross interest carry. Remaining marked lots are not an exhaustive causal decomposition.

- Rolling minima use exact calendar anniversaries with first trading close on/after anniversary. The standard rolling CSV inherits policy state, consistent with purchasing units of a running strategy. Fresh-investor state-reset cohorts are reported separately when generated. Overlapping cohorts are descriptive, not probabilities.

## Figures

![Wealth](capital_reserve_wealth.png)

![Reserve balance](capital_reserve_balances.png)

![Drawdowns](capital_reserve_drawdowns.png)

![Deployment](capital_reserve_deployments.png)

![Full-cycle attribution](capital_reserve_full_cycles.png)

## Reproduce

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m letf.capital_reserve --offline
```

Inputs restore from the existing verified bundle; the manifest records hashes, parameters and runtime versions. Core return data and earlier reports are unchanged. Synthetic pre-inception LETF returns, early index proxies, idealized closing executions, gross 1× returns and the original financing assumptions remain limitations. Cash accrual is not a traded bill fund with mark-to-market risk.
