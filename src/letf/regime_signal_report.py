"""Compact, reproducible report for the prespecified regime experiment."""
from .analysis import sha
from .regime_signals import SMA

LABELS = {SMA:'SMA 200', 'VOL_BINARY':'Absolute vol 20', 'REL_VOL':'Relative vol',
          'EFFICIENCY':'Efficiency 60', 'TREND_QUALITY':'Trend quality 120',
          'LOW_CHURN':'Low churn 60', 'AO':'AO 5/34',
          'UPRO_ALWAYS':'UPRO always', 'SP500_1X':'S&P 1×'}
ORDER = list(LABELS)


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |',
                      '| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(map(str,row))+' |' for row in rows])


def write_report(root, metrics, subperiods, states, agreement, ao_note, checks):
    m = metrics.set_index(['series','lag','switch_cost_bps'])
    names = [n for n in ORDER if n in metrics.series.unique()]
    pct = lambda x: f'{100*x:.2f}%'
    pp = lambda x: f'{100*x:+.2f}'
    signals = [n for n in names if n not in ('UPRO_ALWAYS', 'SP500_1X')]
    rows=[]
    for n in names:
        a,b=m.loc[(n,'LAG1',25)],m.loc[(n,'LAG2',25)]
        rows.append([LABELS[n],pct(a.cagr),pct(b.cagr),pct(b.annualized_volatility),
                     pct(b.max_drawdown),f'{b.sharpe:.2f}',f'{b.average_equity_exposure:.2f}×',
                     int(b.switch_count),pp(b.timing_cagr_difference),pp(b.fee_equal_timing_cagr_difference)])
    robustness=[]
    for n in signals:
        g=metrics[metrics.series==n]
        s=subperiods[subperiods.series==n]
        robustness.append([LABELS[n],f'{g.fee_equal_timing_cagr_difference.min()*100:+.2f} to '
                           f'{g.fee_equal_timing_cagr_difference.max()*100:+.2f}',
                           f'{sum(g.fee_equal_timing_cagr_difference>1e-8)}/4',
                           f'{sum(s.fee_equal_timing_cagr_difference>1e-8)}/16'])
    periods=['1987_1999','2000_2009','2010_2019','2020_latest']
    s=subperiods[(subperiods.lag=='LAG2')&(subperiods.switch_cost_bps==25)].set_index(['series','period'])
    subrows=[[LABELS[n]]+[pct(s.loc[(n,p),'cagr']) for p in periods] for n in names]
    timingrows=[[LABELS[n]]+[pp(s.loc[(n,p),'fee_equal_timing_cagr_difference']) for p in periods]
                for n in signals]
    state_rows=[]
    for n in signals:
        for state in ('favorable','unfavorable'):
            r=states[(states.series==n)&(states.lag=='LAG2')&(states.state==state)].iloc[0]
            state_rows.append([LABELS[n],state,pct(r.fraction_days),pct(r.annualized_sp500_log_return),
                               pct(r.annualized_upro_log_return),pct(r.realized_volatility),
                               pct(r.annualized_exact_path_drag_log)])
    a=agreement[agreement.lag=='LAG1'].set_index(['signal_a','signal_b'])
    overlaps=[[LABELS[n],pct(a.loc[(n,SMA),'agreement_fraction'])] for n in signals]
    cohortrows=[]
    for n in names:
        r=m.loc[(n,'LAG2',25)]
        cohortrows.append([LABELS[n],pct(r.cohort_20y_min_cagr),pct(r.cohort_30y_min_cagr),
                           pct(r.matched_cohort_20y_min_cagr),pct(r.matched_cohort_30y_min_cagr)])
    text = '''# Regime signals for leverage management

The **absolute 20-day volatility rule and the existing 200-day SMA remain the only
signals with positive full-window timing value under all four lag/cost combinations**,
including the stricter control with equal total fee spending. No new indicator clearly
outperforms SMA across execution assumptions and historical subperiods. This supports a
broader leverage-regime mechanism, but not the proposition that any sensible trend or
path-quality indicator will capture it.

## Design and scope

- Primary and only invested family: archived UPRO BASE / S&P 500 1×; favorable = 3×,
  unfavorable = 1×. No cash variants, fitted thresholds, parameter search or new histories.
- Full **common comparison window**: entry close 1986-09-26; returns 1986-09-29 through
  2026-09-02 (10,059 sessions). This preserves the earlier falsification comparison window,
  whose shared warm-up was constrained by other families; it is not all available S&P
  history back to 1980. Earlier archived S&P observations warm up signals only.
- SMA retains the existing total-return-level convention. Efficiency, regression and
  sign flips use frozen S&P price closes. Absolute/relative volatility use S&P total
  returns, sample standard deviation and 252-session annualization. This intentional
  preservation of the SMA baseline means indicator comparisons are not a pure
  price-versus-total-return-controlled experiment.
- VOL_BINARY reuses the existing 20-return rule: volatility <20% = favorable, otherwise
  unfavorable. Its historical performance is reproduced, not extended into a new grid.
- Relative volatility: SD20/SD120 <=1. Efficiency: 60 changes, SER>0 and ER>=0.25;
  flat paths have ER=SER=0. Regression: 120 log-price observations, positive slope and
  OLS slope t>=2 (118 residual degrees of freedom). The t-score is descriptive: serial
  dependence in log-price levels makes an IID significance interpretation inappropriate.
- Churn: 59 adjacent pairs within 60 daily price returns; zero returns are not opposite
  signs. The expanding median begins with the first valid flip statistic and excludes
  the current close's statistic. Equality is unfavorable. It is a sign-alternation proxy,
  not a magnitude-aware measure of volatility or total path length.
- {ao_note}
- Close-t features first affect the return ending t+1 (LAG1) or t+2 (LAG2). Features are
  lagged exactly once; warm-up uses no future values. LAG1 retains the previous idealized
  signal-close execution assumption, not an observed executable intraday fill.
- Costs of 0 or 25 bp apply once per sleeve transition, for selling and buying combined;
  establishment is excluded. Subperiods retain live positions and boundary transition
  costs. All strategies share dates. State statistics describe the returns following
  the lagged state, before switching costs.

## Full-window results

25 bp per switch. Last two columns are strategy minus matched-control CAGR in percentage
points; other risk metrics refer to LAG2. Terminal wealth and all four scenarios are in
`regime_signal_metrics.csv` (wealth is both a multiple and dollars per $10,000).

{full_table}

The ranking by evidence is **SMA and absolute volatility as the leading pair**, then
trend quality as a weaker partial regime classifier; relative volatility and AO lack
cost/lag robustness, while efficiency and sign churn provide little support here.
SMA offers stronger drawdown protection and better worst long cohorts than absolute
volatility. Absolute volatility has much less sensitivity to an extra execution day:
at 25 bp, its CAGR is about 15.8% under both lags, while SMA falls from 17.9% to 15.1%.
These are different strengths, not a clean dominance result. Even these rules retain
severe drawdowns (approximately 78% and 88% under LAG2/25 bp).

## Matched leverage: is the allocation timing productive?

For each signal and lag, the primary control holds constant effective leverage
`L = mean(1 + 2 × favorable)` with daily resets. As in the capital-reserve experiment,
funding plus spread is inferred from the archived UPRO return identity:
`funding = (3*r_sp500 - r_upro - fee)/2`, then
`r_control = L*r_sp500 - (L-1)*funding - fee`.
The existing UPRO expense rate is charged in full. No underlying or LETF series is rebuilt.
Controls have zero regime switches and no switching cost; this is an idealized constant
leverage financing control, not a claimed available ETF.

Because the rotating strategy pays fund expenses only on UPRO days, the CSV also includes
an **equal-fee control**: its constant expense rate is reduced so total simple fee spending
equals the rotating strategy's. This prevents avoided fund fees from masquerading as timing.
For the 1× benchmark, the primary full-fee control mechanically loses an ETF fee; its
equal-fee timing difference is zero. Do not interpret that primary difference as skill.

Average exposure is calculated separately within every reported subperiod; full-window
controls are fitted only to the full-window mean. These are ex-post diagnostics, not
forecastable allocation rules. Equal average leverage does not equalize volatility,
and neither control proves causality or future out-of-sample profitability.

{robust_table}

The full-window SMA and absolute-volatility advantages survive equalizing fees and
25 bp costs. At LAG2/25 bp they are about **+0.52 and +1.19 CAGR points**, respectively.
However, neither wins in every era. Relative volatility has a positive full-window
result only at LAG2/zero cost; AO's corresponding advantage is negligible. More frequent
switching further weakens both. Trend quality has just 73 switches but still fails the
full-window matched-leverage test, so its shortfall cannot be blamed primarily on costs.

## Historical subperiods

LAG2, 25 bp; CAGR. The 1987 start omits the partial 1986 tail included in the full window.

{sub_table}

Same scenarios, **CAGR advantage over the period-specific equal-fee, matched-exposure
control**, percentage points:

{timing_table}

SMA's benefit is concentrated in adverse compounding eras, especially 2000–2009; it
sacrifices considerable wealth in some strong-market periods. Absolute volatility is
more execution-stable but has negative within-period timing differences in three of
these four LAG2/25 bp eras. Its positive full-window difference can coexist with this:
full-window matching also captures how much leverage is allocated *between* eras, while
period-specific matching removes that channel. Counts across scenarios are correlated
observations, not independent statistical replications.

## Conditional states and economic mechanism

LAG2 states. Log-return columns are **252 × mean daily log return**, not calendar CAGR
of a continuous investment in disconnected state dates. Volatility is annualized standard
deviation of the S&P daily returns assigned to that state. Drag is exact
`3*log(1+r_sp500) - log(1+3*r_sp500)`, excluding financing and expenses; daily arithmetic
and log means, approximate drag and financing drag are also in the state CSV.

{state_table}

SMA and absolute volatility most clearly identify the requested mechanism: in their
unfavorable states, S&P log growth remains roughly 10.8–11.4% annualized while UPRO log
growth falls below zero. Unfavorable-state S&P volatility is roughly 29–31%, versus
13–14% when favorable, and exact path drag rises sharply. Financing further reduces
leveraged growth. Thus it is not necessary for equities themselves to have negative
expected log growth for high leverage to be poorly compensated.

Relative volatility separates risk somewhat, but its unfavorable-state UPRO log growth
remains positive. A relative ratio can label persistently high volatility as favorable
once the long-window denominator catches up; absolute volatility directly addresses the
level of the compounding penalty. The ratio does not explain more in this experiment.

Efficiency selects only about 14% of days for leverage, and its supposedly favorable
state has *lower* subsequent UPRO log growth. Regression quality identifies lower
volatility but insufficient improvement in subsequent leveraged growth. Low churn barely
separates realized volatility or path drag; alternating signs without return magnitudes
are a poor proxy for the quadratic compounding penalty. AO separates volatility but
fails to deliver robust net matched-leverage gains. The evidence supports positive equity
growth plus controlled volatility as the economic foundation; it does **not** establish
high observed efficiency, low sign churn, or faster momentum as additional useful triggers.

**Before quoting any advantage below:** `reports/signal_null_model_results.md`
tests these strategies against a null with the same switch count, episode
lengths and leveraged-day fraction but randomized timing. None is significant
at 5% after correcting for the 144 nuisance-parameter cells searched here, and
every one has a top-20-session share above 100% of its total advantage — the
remaining ~10,000 sessions are net negative.

## Signal overlap and long-horizon check

LAG1 agreement with the existing SMA; the compact CSV contains the complete pairwise
matrix in long form for both lags. Raw agreement depends on how often each rule is
favorable and does not establish incremental predictive information.

{overlap_table}

Absolute volatility agrees with SMA about 86% of the time, supporting substantial
shared regime information. Regression quality agrees about 85% yet has inferior
matched-leverage performance: broad classification similarity is not sufficient, and
the dates of disagreements matter. No optimized combined rule is warranted by this test.

Worst exact-anniversary monthly-entry cohort CAGR, LAG2/25 bp. Controls here keep the
full-window matched leverage fixed (not rematched within every cohort); the existing
cohort machinery is reused. There are 240 20-year and 120 30-year cohorts, with substantial
overlap, so these are descriptive historical checks rather than independent trials.

{cohort_table}

## Reproduction and limitations

Run `PYTHONPATH=src python -m letf.regime_signals` from the repository root. It uses only
verified frozen bundles, leaves their bytes unchanged, and writes the five requested
compact reports. No daily-output files or summary figure are needed. The generator
checks {checks} reused baseline rows against the prior committed results. Tests:
`PYTHONPATH=src python -m unittest discover -s tests -p 'test_regime_signals.py' -v`.

This is a prespecified historical falsification exercise on one equity family, not new
held-out validation. Earlier source-proxy, financing, gross-1×, synthetic-fund and
execution assumptions remain in force; taxes and actual market impact are not modeled.
There is no Nasdaq replication or combined-signal fit in this focused experiment.

Runtime versions and source hashes are in `reports/regime_signal_manifest.json`.
They are deliberately not repeated here: this report must be byte-identical when
regenerated, and a version string cannot be.

Verified input SHA-256:

{hashes}
'''
    paths=['config.json','data/snapshots/portfolio_sma_inputs.zip','data/snapshots/sma_price_inputs.zip']
    hashes='\n'.join(f'- `{p}`: `{sha(root/p)}`' for p in paths)
    text=text.format(ao_note=ao_note,checks=checks,hashes=hashes,
        full_table=table(['Signal','CAGR LAG1','CAGR LAG2','Vol LAG2','Max DD LAG2','Sharpe','Mean exposure','Switches','Timing Δ pp','Equal-fee Δ pp'],rows),
        robust_table=table(['Signal','Equal-fee full-window Δ range, pp','Positive full scenarios','Positive subperiod scenarios'],robustness),
        sub_table=table(['Signal',*periods],subrows),timing_table=table(['Signal',*periods],timingrows),
        state_table=table(['Signal','State','Day share','S&P log','UPRO log','S&P vol','Path drag'],state_rows),
        overlap_table=table(['Signal','Agreement with SMA'],overlaps),
        cohort_table=table(['Signal','20y min','30y min','Control 20y min','Control 30y min'],cohortrows))
    (root/'reports/regime_signal_results.md').write_text(text)
