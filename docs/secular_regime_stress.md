# Secular regime stress and adaptive risk allocation

This independent experiment changes the economic assumptions of the existing joint bootstrap instead of merely reordering history. Motivated by Smolyansky (2023), [End of an era](https://doi.org/10.17016/FEDS.2023.041), and the competing possibility of AI-driven productivity growth.

## Run

```bash
pip install -e .
python -m unittest discover -s tests -p test_secular_regime_stress.py -v
python -m letf.secular_regime_stress --paths 100 --years 20
python -m letf.secular_regime_stress --paths 100 --years 30 --block 126
```

Outputs are deliberately uncommitted research results in data/processed/secular_regime_stress/ (use --output to separate runs). Run the small experiment first; increase the paired path count before making tail claims. The historical reports and their regeneration machinery are unchanged.

- summary.csv: per-regime median and fifth-percentile real CAGR, drawdown, underperformance of bills and SPX, Pareto flag and regret.
- paths.csv: paired path-level outcomes.
- detection.csv and detection_summary.csv: state agreement, excess or insufficient risk, switches, first correct classification delay, already-in-new-state fraction, and missed shifts conditional on not already being in that state.
- cross_regime.csv: worst-regime return, drawdown probability, maximum median regret and frontier survival.
- manifest.json: scenario assumptions, seed and settings.

## Design

Prespecified stationary regimes include favorable historical-like returns, normalization, slowdown, stagnation, fiscal stress, AI growth, AI growth with expensive capital, and exceptional productivity. Four switches occur five years after entry, with a separate one-year valuation shock on adverse transitions. An unchanged joint historical bootstrap is the control. The same sampled block indices are used for all cases.

Equity log returns are shifted by population-level means and scaled around the historical mean; price and total-return adjustments are identical, preserving their daily dividend gap. No path is forced to hit its target. Nasdaq excess growth is independently specified. Cash carry and funding shift together, and the long Treasury residual is recombined with the equity innovation to target a stock/bond correlation. This last step is a reduced-form return model, not a structural yield-curve model: it does not independently price duration, term premium, or transition-related bond repricing. The constant inflation assumption is 2.5%; real returns are inflation-deflated at the endpoint. Historical-control drawdowns are nominal, as are all other drawdowns. Realized log-return targets are not guaranteed per path.

## Decision layer

At each observed calendar-quarter end:
- Rate/volatility rule estimates prospective log excess return from a fixed 8% nominal geometric equity anchor minus trailing 63-session cash carry, and estimates annualized variance from 126 sessions of prices.
- Combined rule places 25% weight on trailing 252-session equity total log return, and gates green on being above the 200-session price SMA.
- Red/amber/green are thresholds for whether estimated excess return clears the 2x and 3x versus 1x log-growth hurdles, including 50 bp financing spread. These are screening rules, not exact growth-maximizing leverage.
- Test 1, 2 and 4 consecutive quarterly confirmations, a full session execution delay, and 25 bp switching costs. Initial state is amber.
- ETF policy uses 1x/2x/3x; LEAPS policy uses index exposure / 80%-strike 25%-premium / 90%-strike 40%-premium with bills. A separate red-to-bills policy is included. Switches sell the actual call and open the new structure, never splice the returns of hypothetical continuously invested strategies.
- Static comparisons: both indices, synthetic 2x/3x, 3x/1x and 3x/bills SMA, four option structures with either bills or long bonds, and bills and long bonds alone. A labelled infeasible oracle knows the latent regime; it is excluded from the frontier.

No future observations enter a feasible signal. The estimated regime is not an observable fact: the oracle's labels are only the assumed population return/variance hurdles for SPX, not the ex post optimal option structure. A few quarters can identify changes in financing costs or volatility more readily than a change in expected equity returns; the experiment specifically measures that distinction.

## Limitations and follow-up

A 25% weight on past one-year returns is a prespecified diagnostic rather than a validated forecast. The common SPX signal does not infer Nasdaq-specific growth. The current research code does not include point-in-time earnings revisions, valuation, real yields or productivity data; these are promising follow-up signals requiring historical publication dates, revisions/vintages, and walk-forward calibration. Options still use the existing modeled IV and rates proxy. The synthetic 2x Nasdaq exposure uses the configured Nasdaq fund expense as a proxy, and levered returns are reconstructed with the existing financing proxy (not historical tradeable prices). ETF policy exposures are idealized and don't model a fund relisting after a wipeout. The LEAPS implementation uses the repository's existing calendar and option spread conventions; it does not cover all strikes, maturities, or dip-buying rules. No regime probabilities are assigned. A Pareto flag does not imply statistical significance or future outperformance.

Next: run larger path counts, vary spread/confirmation/return anchor and block length, add a regime-specific return/rate grid and break-even premiums, evaluate the full lattice and latest dip-buying variants, build a vintage-aware macro signal comparison, and expose generated results in the interactive site only after validation.


## Nasdaq decision extension

The four Nasdaq and SPX option structures (80/25, 85/30, 90/35, 90/40) now run against bills, long Treasuries, 50/50 reserve and a quarterly duration gate. The gate assigns half the reserve to long bonds only when their trailing 126-session correlation with SPX is negative and their trailing return exceeds bills; otherwise all reserve capital stays in bills. It applies with the same full-session delay and charges 5 bp per unit of duration turnover. The blended reserve is a daily target-weight approximation, not a new tradable fund or a modeled yield curve. The Nasdaq adaptive call menu additionally uses 85/30 amber and 90/40 green, compares a common SPX signal against Nasdaq's own signal, and separates the duration decision from the option decision.

nasdaq_decision.csv contains every feasible Nasdaq variant, including paired probability of underperforming NDX_1X. The dedicated Secular regime decision experiment workflow runs a small smoke test on branch updates and can be manually dispatched at higher path counts. Twelve paths do not establish meaningful tail probabilities.

The immediate decision should be conditional on actual available option bid/ask quotes, maturity and implied volatility, the investor's maximum tolerable portfolio drawdown, and the maturity of the Treasury reserve. Historical return superiority is not an estimate of prospective performance. No trade instruction is generated from these experiments.


## Decision-stage experiment

The large workflow uses 16 independently seeded shards of 128 paths, or 2,048 paired paths per scenario at 20 years. It recomputes quantiles, risk constraints, and paired comparisons from raw path outcomes rather than averaging shard summaries. It validates disjoint seeds, identical settings and complete regime/strategy coverage. Outputs include paired_decisions.csv (incremental CAGR, probability of winning, extra drawdown), and constrained_decisions.csv (best median or fifth-percentile CAGR under illustrative drawdown-probability limits). The constraints are not the investor's stated preferences.

A separate forward_regime_signal module accepts point-in-time estimates of forward P/E, per-share earnings growth, dividend yield, cash yield, realized volatility and inflation. It tests earnings growth +/-3 percentage points and terminal valuation ratios of 70%, 85%, 100% and 115% of initial valuation over an explicit horizon. It computes the EPS growth needed for 2x and 3x constant-leverage hurdles. It rejects estimates published after the decision date. The hurdle is a screening diagnostic, not a direct valuation of rolling options. No actual historical earnings estimates or current option quotes are supplied; independent vendor data and vintage-aware walk-forward evaluation are still required.

```bash
python -m letf.forward_regime_signal estimates.csv --horizon 10 --output forward_screen.csv
python -m letf.nasdaq_entry_decision quotes.csv --bill-yield 0.04 --long-yield 0.05 --output entry_stress.csv
```

Rates in these example commands are hypothetical. Required columns and input units are in the modules' docstrings. Neither module generates a trade instruction.
