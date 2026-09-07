"""Compact presentation of the prespecified analysis (no rankings/optimization)."""
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

from .model import wealth
from .pipeline import table


def pct(x):
    if pd.isna(x):
        return '—'
    return f'{100*x:.3f}%' if -1 < x < -.999 else f'{100*x:.1f}%'


def compact(frame, columns):
    out = pd.DataFrame()
    for source, label, fmt in columns:
        out[label] = frame[source].map(fmt) if fmt else frame[source]
    return table(out)


def write_report(root, config, static, sma, cohorts, calendar):
    out = root/'reports'
    pm = pd.read_csv(out/'portfolio_metrics.csv').query("rebalance == 'quarterly'")
    sm = pd.read_csv(out/'sma_strategy_metrics.csv').query("window == 'common'")
    lm = pd.read_csv(out/'sma_length_sensitivity.csv').query("window == 'common'")
    fm = pd.read_csv(out/'sma_financing_sensitivity.csv').query("window == 'common'")
    pf = pd.read_csv(out/'portfolio_financing_sensitivity.csv')
    regimes = pd.read_csv(out/'sma_regime_analysis.csv')
    metric_cols = [('series','Strategy',None),('cagr','CAGR',pct),('terminal_multiple','Wealth ×',lambda x:f'{x:,.1f}'),
                   ('annualized_volatility','Volatility',pct),('max_drawdown','Max DD',pct),
                   ('longest_underwater_calendar_days','Longest underwater (y)',lambda x:f'{x/365.25:.1f}'),
                   ('sharpe','Sharpe',lambda x:f'{x:.2f}')]
    rollcols = [('series','Strategy',None),('cohort_20y_min_cagr','20y min CAGR',pct),
                ('cohort_20y_median_cagr','20y median',pct),('cohort_30y_min_cagr','30y min CAGR',pct),
                ('cohort_30y_median_cagr','30y median',pct)]
    selected = ['SP500_1X','UPRO_ALWAYS','UPRO_SMA_TO_SP500','UPRO_SMA_TO_TBILL']
    prior = calendar[calendar.get_loc(sma.index[0])-1]
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.spines.top':False,'axes.spines.right':False})
    fig, axes = plt.subplots(2,2,figsize=(14,10),constrained_layout=True)
    colors = ['#525b68','#bc4949','#258679','#3155a6']
    for name, color in zip(selected,colors):
        nav = pd.concat([pd.Series([1.],index=[prior]),wealth(sma[name])])
        axes[0,0].plot(nav.index,nav,label=name,color=color,lw=1.15)
        axes[0,1].plot(nav.index,nav/nav.cummax()-1,color=color,lw=1.05)
        c = cohorts.query("window == 'common' and horizon_years == 20 and series == @name")
        axes[1,0].plot(pd.to_datetime(c.entry_close),c.cagr,color=color,lw=1.2)
    axes[0,0].set_yscale('log'); axes[0,0].set_title('Growth of $1 · log scale'); axes[0,0].legend(fontsize=8)
    axes[0,1].set_title('Drawdown from prior peak'); axes[0,1].yaxis.set_major_formatter(PercentFormatter(1))
    axes[1,0].set_title('20-year CAGR by entry close · overlapping cohorts'); axes[1,0].yaxis.set_major_formatter(PercentFormatter(1))
    ax=axes[1,1]
    for i,row in enumerate(pm.itertuples()):
        ax.scatter(row.annualized_volatility,row.cagr,s=30,color='#258679')
        ax.annotate(str(i+1),(row.annualized_volatility,row.cagr),xytext=(4,3),textcoords='offset points',fontsize=8)
    ax.set_title('Static portfolios · labels follow table row numbers')
    ax.set_xlabel('Annualized volatility'); ax.set_ylabel('CAGR')
    ax.xaxis.set_major_formatter(PercentFormatter(1)); ax.yaxis.set_major_formatter(PercentFormatter(1))
    for ax in axes.flat: ax.grid(alpha=.15)
    fig.suptitle(f'Prespecified 200-day SMA and quarterly portfolios · {prior.date()}–{sma.index[-1].date()}\nSynthetic histories, 50 bp spread; nominal returns before taxes and trading costs',fontsize=12)
    fig.savefig(out/'portfolio_sma_overview.png',dpi=160); plt.close(fig)
    static_table = pm.copy()
    static_table['series'] = [f'{i+1}. {n}' for i,n in enumerate(pm.series)]
    state = sm[sm.series.str.contains('SMA_TO')]
    statecols = [('series','Strategy',None),('fraction_leveraged','Leveraged days',pct),
                 ('switches','Switches',lambda x:f'{x:.0f}'),('switches_per_year','Per year',lambda x:f'{x:.1f}'),
                 ('average_risk_on_episode_sessions','On sessions',lambda x:f'{x:.1f}'),
                 ('average_risk_off_episode_sessions','Off sessions',lambda x:f'{x:.1f}'),
                 ('risk_on_state_cagr','On-state CAGR',pct),('risk_off_state_cagr','Off-state CAGR',pct)]
    sensitivity=[]
    for name in sm.series:
        vals=lm[lm.series==name].set_index('sma_days')
        spreads=fm[(fm.series==name)&(fm.sma_days==200)].set_index('spread_bps')
        allvals=fm[fm.series==name]
        sensitivity.append({'Strategy':name,'150d CAGR':pct(vals.loc[150,'cagr']),
                            '200d CAGR':pct(vals.loc[200,'cagr']),'250d CAGR':pct(vals.loc[250,'cagr']),
                            '0/50/100 bp CAGR':' / '.join(pct(spreads.loc[b,'cagr']) for b in (0,50,100)),
                            'DD range, all 9':f"{pct(allvals.max_drawdown.min())} to {pct(allvals.max_drawdown.max())}"})
    rebal = pd.read_csv(out/'portfolio_metrics.csv')
    static_sensitivity=[]
    for name in pm.series:
        a=rebal[rebal.series==name].set_index('rebalance')
        b=pf[pf.series==name].set_index('spread_bps')
        static_sensitivity.append({'Strategy':name,'Monthly / quarterly / annual CAGR':' / '.join(pct(a.loc[f,'cagr']) for f in ('monthly','quarterly','annual')),
                                   '0 / 50 / 100 bp CAGR':' / '.join(pct(b.loc[s,'cagr']) for s in (0,50,100))})
    regime_text=[]
    for family, under in [('UPRO','SP500'),('SSO','SP500'),('TQQQ','NASDAQ100')]:
        names=[f'{under}_1X',f'{family}_ALWAYS',f'{family}_SMA_TO_'+('NASDAQ' if under=='NASDAQ100' else under),f'{family}_SMA_TO_TBILL']
        rows=regimes[(regimes.kind=='sma')&(regimes.window=='common')&regimes.series.isin(names)]
        rows=rows.sort_values(['regime','series'])
        regime_text.append(f'### {family}\n\n'+compact(rows,[('regime','Episode',None),('series','Strategy',None),
            ('episode_max_drawdown','Peak-to-trough',pct),('episode_terminal_multiple','End / start',lambda x:f'{x:.2f}×'),
            ('recovery_date','Peak recovered',lambda x:'Unrecovered' if pd.isna(x) else str(x)),
            ('recovery_days_from_peak','Peak to recovery (y)',lambda x:'—' if pd.isna(x) else f'{x/365.25:.1f}')]))
    extended=pd.read_csv(out/'sma_strategy_metrics.csv').query("window != 'common'")
    extcols=[('window','Window',None),('entry_close','Entry',None),('series','Strategy',None),('cagr','CAGR',pct),('max_drawdown','Max DD',pct)]
    interpretation=[]
    lookup=sm.set_index('series')
    for fund,label in [('SSO','SP500'),('UPRO','SP500'),('TQQQ','NASDAQ')]:
        a,b,c=[lookup.loc[n] for n in [f'{fund}_ALWAYS',f'{fund}_SMA_TO_{label}',f'{fund}_SMA_TO_TBILL']]
        interpretation.append(f"- **{fund}:** always leveraged CAGR {pct(a.cagr)}; rotate to 1× {pct(b.cagr)}; rotate to bills {pct(c.cagr)}. "
                              f"Maximum drawdowns are {pct(a.max_drawdown)}, {pct(b.max_drawdown)}, and {pct(c.max_drawdown)}, respectively. "
                              f"Bills / 1× terminal wealth is {c.terminal_multiple/b.terminal_multiple:.2f}× over the same dates.")
    findings = []
    for fund, label in [('SSO','SP500'),('UPRO','SP500'),('TQQQ','NASDAQ')]:
        a, b, c = [lookup.loc[n] for n in [f'{fund}_ALWAYS',f'{fund}_SMA_TO_{label}',f'{fund}_SMA_TO_TBILL']]
        findings.append(f"{fund}: 1× rotation adds {(b.cagr-a.cagr)*100:.1f} percentage points to CAGR; "
                        f"bills add {(c.cagr-a.cagr)*100:.1f} points. Choosing bills over 1× costs "
                        f"{(b.cagr-c.cagr)*100:.1f} points of CAGR and reduces full-period wealth by "
                        f"{100*(1-c.terminal_multiple/b.terminal_multiple):.0f}%.")
    conclusions = "\n\n".join(findings)
    static_lookup = pm.set_index('series')
    a,b,c,d=[static_lookup.loc[n] for n in ['SSO60_LT40','UPRO50_LT50','UPRO55_TMF45','UPRO60_TMF40']]
    static_interpretation = (
        f"Leveraged stock/bond mixes are not uniformly superior. UPRO55_TMF45 and UPRO60_TMF40 "
        f"produce {pct(c.cagr)} and {pct(d.cagr)} CAGR versus {pct(a.cagr)} for SSO60_LT40 and "
        f"{pct(b.cagr)} for UPRO50_LT50, but their maximum drawdowns are {pct(c.max_drawdown)} / "
        f"{pct(d.max_drawdown)} versus {pct(a.max_drawdown)} / {pct(b.max_drawdown)}. "
        "The more bond-heavy UPRO40_TMF60 does not improve on UPRO50_LT50 in either full-period "
        "CAGR or maximum drawdown. TQQQ/bond combinations achieve higher historical CAGR but "
        "still have extreme drawdowns. These are different total leverage and risk exposures, "
        "not controlled estimates of a diversification premium.")
    report=f'''# Portfolio and 200-day SMA results

Data through **{config['as_of']}**. All main tables use the **same entry close, {prior.date()}, and endpoint, {sma.index[-1].date()}**.
Baseline financing spread is 50 bp per borrowed dollar; synthetic BASE economics, nominal USD, before investor taxes and trading costs.
No weights, signals, or lookbacks were optimized. **200 days is primary; 150/250 days are robustness checks.**

**Core distinction:** UPRO → SP500 below SMA changes leverage while maintaining equity exposure.
UPRO → T-bills changes both leverage and equity exposure. These are different economic decisions.
The same distinction applies to SSO and TQQQ.

## Primary results

{compact(sm,metric_cols)}

{chr(10).join(interpretation)}

## Static portfolios: quarterly rebalancing

{compact(static_table,metric_cols)}

All 14 allocations are fixed in `letf.analysis.PORTFOLIOS`. The 1× long-Treasury sleeve is not cash.
Portfolio rebalancing is separate from daily leverage resets inside LETFs.

## Long-horizon entry dependence

### SMA strategies

{compact(sm,rollcols)}

### Static portfolios

{compact(pm,rollcols)}

Monthly entry closes, first trading close on/after each exact 20/30-year anniversary.
The CSVs also contain maximum CAGR and minimum/median terminal wealth. Cohorts overlap;
they are **descriptive historical windows, not independent trials or future probabilities**.
Entries buy the ongoing strategy: static sleeve weights may have drifted since the last
scheduled rebalance; SMA positions inherit the pre-entry signal. No cohort restarts signal warm-up.

## Switching and state attribution

{compact(state,statecols)}

Fractions count trading sessions. Each state receives its entire close-to-close calendar
interval, including weekends. State CAGRs annualize compounded returns by **calendar time
spent in that state**, omitting all other periods; they are conditional, discontinuous-time
summaries, not standalone investable CAGRs. The CSV additionally gives additive annual log-growth
contributions. First/last episodes are included and may be censored. Initial allocation is
not a switch; a risk-on/off change counts once, not twice for selling and buying.

## Prespecified sensitivities

{table(pd.DataFrame(sensitivity))}

All lookbacks and financing spreads use identical dates and the same underlying signal.
Financing scenarios replace the leveraged sleeve **only while that sleeve is held**.
The full 3 × 3 grid, downside metrics, and 20/30-year summaries are in
`sma_financing_sensitivity.csv`. `sma_length_sensitivity.csv` contains the baseline-spread slice.

{table(pd.DataFrame(static_sensitivity))}

## Stress regimes

Episode windows are full calendar 1987, 2000–2002, 2007–2009, 2020, and 2022.
Peak-to-trough includes the close immediately before the episode and peaks reached inside it.
End/start is the compounded episode wealth ratio. Recovery follows the peak associated with
that episode's maximum drawdown and can occur after the episode ends; blank recovery means
not regained by {config['as_of']}. A peak regained and subsequently lost again still counts as a recovery.
These episode drawdowns differ from a full-history underwater loss when an earlier peak
predates the episode. CSV fields separately give end wealth relative to the pre-episode
all-time peak and the first date that peak was regained. Static portfolios and extended
family windows are also included in `sma_regime_analysis.csv`.

{chr(10).join(regime_text)}

## Longer family windows (separate comparisons)

{compact(extended,extcols)}

Do not compare terminal wealth between these windows and the common-window tables.
All comparisons within a window share dates across all three lookbacks, after the 250-level
warm-up; that deliberately discards some otherwise available 150/200-day history.

## Interpretation

{conclusions}

The strongest result here supports **timing leverage intensity while retaining equity exposure**.
At 200 days, both rotations improve CAGR and drawdown versus always-leveraged exposure in
all three families. Staying in 1× equities produces more full-period wealth than bills in
every tested length/spread combination, including the extended S&P window. This ordering
is not universal across entry cohorts: bills have slightly better worst 20-year S&P CAGRs,
while the 1× versions have higher median 30-year outcomes. Bill protection therefore has
an opportunity cost relative to 1× rotation, even though primary-rule CAGR exceeds always-on
leverage. TQQQ's primary rotated drawdowns remain about 96–98%; a higher terminal CAGR
should not obscure the long recovery or the negative worst 20-year cohorts.

{static_interpretation}

Across all nine length/spread combinations, UPRO and TQQQ rotations retain higher CAGR
and smaller drawdowns than always-on leverage. SSO → 1× also retains a CAGR advantage,
but **SSO → bills does not**: 150 days underperforms always-on SSO across spreads, and
250 days also underperforms on the common window. Thus the 2× bill-rotation growth result
is sensitive to the SMA rule. The precise 200-day return advantage is not a universal
property of trend following. The 55/45 and 60/40 UPRO/TMF mixes retain their CAGR lead over
SSO60_LT40 and UPRO50_LT50 in the quarterly financing scenarios, with greater downside.
Rebalancing frequency materially changes outcomes (especially TQQQ combinations); the
quarterly primary result is a specification, not a selected winner. Absolute returns remain
conditional on historical proxies, funding/fee assumptions, and frictionless execution.

## Definitions and safeguards

- Signal: trailing N **observed 1× total-return levels**, including the initial entry-close
  level; close > SMA is risk-on and equality is risk-off. The first position is on the
  session after the Nth level. Position for return t uses only signal t−1. Missing internal
  sessions raise an error rather than being dropped. Execution at the signal close with
  exposure for the next close-to-close return is an idealized daily-bar convention; actual
  close auction timing, overnight moves and slippage require execution-level validation.
- CAGR uses elapsed calendar days / 365.25. Volatility uses daily sample SD × √252.
  Sharpe is mean daily strategy-minus-bill return / its sample SD × √252.
  Best/worst calendar years exclude partial endpoint years. Worst rolling 1/5/10-year CAGR
  considers every eligible daily entry and the first close on/after its anniversary.
- Underwater duration is elapsed calendar time from a high-water mark through recovery,
  including an unrecovered spell through the final close. Maximum-drawdown recovery is
  reported from both peak and trough. Wealth paths include the initial $1, so a loss on
  the first return counts. No missing duration is interpreted as zero.
- **Path-compounding drag** is the gap between ideal daily leveraged log growth and
  leverage times underlying log growth; it is not another fee. **Financing and expense drag**
  is the separate gap between ideal and costed LETF returns, as in the existing decomposition.
- **Leverage timing** is represented by LETF → 1×. The incremental **equity-market timing**
  comparison is LETF → bills versus LETF → 1×: both hold the identical LETF on risk-on days,
  so their log-wealth difference arises entirely on risk-off days. The two variants do not
  by themselves identify timing skill separately from lower average leverage; an exposure-
  matched counterfactual would be needed for that stronger claim.
- **Diversification/rebalancing effects** arise from combining stock/bond sleeves and
  periodically restoring weights. Compare the fixed portfolios and rebalance sensitivities;
  do not ascribe their gains to removing financing costs or volatility drag.
- Existing validation is unchanged. Its holdout validates approximate ETF reconstruction,
  **not** out-of-sample SMA performance. The present strategy comparison is a historical
  backtest on the full archive; choosing it in advance of this run does not make the past
  unseen data. Trading spreads, turnover costs, investor taxes and implementation delays
  are absent. Switch counts help scope those still-unvalidated costs.
- Existing proxies carry through: pre-1988 S&P uses grossed-up VFINX; pre-1999 Nasdaq is
  price-only (so early Nasdaq signals cannot be claimed to use a true observed TR index);
  pre-July-2002 long Treasury uses duration-mismatched VUSTX. Large TMF outcomes are particularly
  dependent on that proxy and the long historical bond bull market. Current fixed expense
  ratios, funding proxy, fund survival, and extreme-day behavior remain model assumptions.

## T-bill source and accrual

`TBILL_3M_1X` uses daily [FRED DTB3](https://fred.stlouisfed.org/series/DTB3), sourced from
Federal Reserve H.15, a **discount-basis** annualized rate. This is distinct from the
existing DFF borrowing proxy. No splice is needed over the equity history.
Following the [Treasury bill pricing convention](https://www.treasurydirect.gov/marketable-securities/understanding-pricing/),
for decimal discount quote d and assumed 91-day maturity:

`P / face = 1 − d × 91 / 360; daily carry = ((face / P) − 1) / 91`.

For each calendar day, use the most recent nonmissing observation dated no later than the
previous calendar date, with forward carry across holidays; compound `(1 + daily carry)`
through the next trading close. The fixed 91-day tenor and daily reinvestment are explicit
proxy assumptions. This is accrued cash carry, **not a marked-to-market 3-month bond index**;
it omits bill price changes, fees and trading costs. The cached historical series is not a
vintage-by-vintage record of intraday publication availability.
Raw cash bytes, URL, timestamp and SHA-256 are in `source_manifest.json`; analysis input hashes,
configuration hash and frozen input-bundle hash are in `portfolio_sma_manifest.json`.

## Reproduce and inspect

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m letf.analysis --offline
```

A clean clone can rebuild this analysis offline from `data/snapshots/portfolio_sma_inputs.zip`.
It restores only the existing daily-return archive and raw DTB3 cache, verifies their hashes,
and does not regenerate underlying histories. Existing processed inputs are never overwritten.
For a deliberately updated analysis, first rebuild underlying series with the existing pipeline,
then run `python -m letf.analysis --refresh` to refresh cash and capture the new input bundle.
Default online analysis downloads only DTB3 when missing and reuses cached daily histories.

Compact metrics and sensitivity CSVs are under `reports/`. Daily portfolio/rotation returns,
positions, cash returns, and individual cohort rows are generated under `data/processed/`.
All return fields are decimal units; wealth is a multiple of starting capital.

![Wealth, drawdown, rolling cohorts and static risk/return](portfolio_sma_overview.png)
'''
    (out/'portfolio_and_sma_results.md').write_text(report)
