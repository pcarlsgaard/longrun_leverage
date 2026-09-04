"""Compact, reproducible interpretation of prespecified falsification outputs."""
from pathlib import Path
import json

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

from .analysis import path


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |', '| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(map(str,row))+' |' for row in rows])


def pct(v): return '—' if pd.isna(v) else f'{v:.1%}'
def num(v): return '—' if pd.isna(v) else f'{v:,.1f}'


def write_report(root, primary, underlying, state_vol, calendar):
    reports=root/'reports'
    read=lambda name:pd.read_csv(reports/f'{name}.csv')
    lag=read('sma_execution_lag'); cost=read('sma_switching_costs')
    grid=read('sma_falsification_grid'); sub=read('sma_subperiods')
    all_signal=read('sma_signal_source_comparison')
    signal=all_signal[all_signal.signal_window=='observed_tr_only']; vol=read('sma_volatility_state_analysis')
    alternatives=read('volatility_target_comparison'); attr=read('sma_attribution')
    stress=read('sma_stress_event_detail'); delay=read('sma_stress_delay_transitions')
    manifest=json.loads((reports/'sma_falsification_manifest.json').read_text())
    def result(name,l='LAG1',bps=0):
        return cost[(cost.series==name)&(cost.lag==l)&(cost.switch_cost_bps==bps)].iloc[0]
    u1=result('UPRO_SMA_TO_SP500'); u2=result('UPRO_SMA_TO_SP500','LAG2'); ua=result('UPRO_ALWAYS')
    body=[f'''# SMA falsification results

**Verdict: UPRO → 1× remains a candidate for further study, but its outperformance is not robust under the full battery.**
It survives the extra session at zero, 10 and 25 bp per switch; at 50 bp **combined with delay** its CAGR falls below always-on UPRO.
The weaker claim—1× risk-off produces more full-history wealth than T-bills—survives the parameter grid,
but reverses in 2000–2009. Neither claim meets the requested “Robust” standard across historical subperiods.
Execution timing materially changes the magnitude, especially around the 1987 crash.

Matched primary entry close **{manifest['entry_close']}**, through **{manifest['as_of']}**. Nominal wealth per $1;
no contributions, withdrawals or investor taxes. The existing synthetic returns, cash accrual, funding assumptions,
portfolio/SMA framework and validation are unchanged. The prior common portfolio window is retained, with sufficient
warm-up for both lags and all three SMA lengths. No parameter was optimized.

## Direct answers

1. **Additional execution day:** UPRO → 1× CAGR falls from {pct(u1.cagr)} to {pct(u2.cagr)}, versus {pct(ua.cagr)} always-on.
   Wealth falls from {num(u1.terminal_multiple)}× to {num(u2.terminal_multiple)}×. Only **{u2.benefit_terminal_wealth_retained_pct:.1f}%**
   of excess terminal wealth survives ({u2.benefit_log_wealth_retained_pct:.1f}% of excess log wealth). This is material timing sensitivity.
2. **Switching friction:** the 200-day UPRO → 1× rule survives 10/25 bp under either lag and 50 bp under LAG1;
   LAG2 + 50 bp reverses the advantage. SSO is more fragile: even LAG2 + 10 bp loses to always-on SSO.
   These are specified cost scenarios, not estimates of actual historical execution costs.
3. **Distinct environments:** LAG1 UPRO → 1× wins three of four blocks; LAG2 wins two.
   Both lose in 2010–2019 and over 2010–latest. The 2000–2009 bear markets generate most of the net log-wealth benefit.
4. **Signal source:** price and total-return signals differ on about {100*signal[signal.family=='SP500'].signal_disagreement_fraction.iloc[0]:.2f}% of matched S&P days.
   The 1×-versus-bills ordering persists, but CAGR magnitude changes. A separate full-archive comparison
   includes 1987: price-SMA delayed CAGR is 19.1%, versus 16.8% for the existing TR signal. The price
   crossing occurs one session earlier before Black Monday; this early comparison also changes proxy construction.
5. **Volatility/leverage economics:** below-SMA days have roughly twice the prior realized volatility.
   Their average arithmetic equity return remains positive, while synthetic UPRO's average log return is negative.
   This supports an unfavorable-leverage-regime interpretation, not simply “stocks tend to fall below trend.”
6. **Explicit volatility alternatives:** the discrete 20% target achieves lower drawdown with much lower average exposure,
   but does not reproduce most of the LAG1 wealth gain. The binary rule reproduces part of the gain with comparable
   average exposure, yet has a much deeper drawdown. Neither comparator identifies a unique causal mechanism.
7. **Attribution:** exact log accounting shows path-compounding and financing savings more than offset forgone leveraged
   equity growth. Positive below-SMA 1× equity growth is worth more than bill carry over the full sample.
8. **1× versus bills:** stronger full-history wealth robustness for 1×, not universal dominance. Bills do better in
   2000–2009, and can improve some worst 20-year cohorts. Drawdown ordering can change under delay.
9. **Thesis:** evidence remains consistent with leverage timing and weakens the case for exiting equities entirely,
   but does not prove timing skill or that SMA is primarily a volatility filter. The candidate remains research-worthy;
   a strong claim of reliable superiority is falsified by the joint cost/delay and regime tests.

## Execution and costs

LAG1 = `IMMEDIATE_NEXT_RETURN`: signal at close t controls return ending t+1.
LAG2 = `WAIT_ONE_TRADING_DAY`: the same signal controls return ending t+2, with the old position retained through t+1.
The extra delay applies symmetrically to entry and exit; it is not a bid/ask charge. LAG1 remains an idealized
close-based convention, not demonstrated after-close execution at the already observed price.
''']
    rows=[]
    for name in ['SSO_ALWAYS','SSO_SMA_TO_SP500','SSO_SMA_TO_TBILL','UPRO_ALWAYS','UPRO_SMA_TO_SP500','UPRO_SMA_TO_TBILL']:
        a,b=result(name),result(name,'LAG2')
        rows.append([name,pct(a.cagr),pct(b.cagr),num(a.terminal_multiple),num(b.terminal_multiple),pct(a.max_drawdown),pct(b.max_drawdown)])
    body.append(table(['Strategy','CAGR L1','CAGR L2','Wealth L1','Wealth L2','Max DD L1','Max DD L2'],rows))
    body.append('''
Each stated cost is charged **once per allocation transition**, covering the portfolio sale/purchase implementation.
An off/on cycle entails two charges: 50 bp per switch costs 99.75 bp per complete cycle. Initial allocation has no
switch charge. Daily net growth is `(1 + gross return) × (1 − cost × transition)`.
Thus “round-trip implementation” describes completing one sleeve replacement, not an entire off/on signal cycle.
No fee is charged for merely remaining below or above SMA. UPRO's 200-day rule switches 230 times, about 5.8/year;
at 50 bp the cost-only terminal wealth factor is `(0.995)^230 = 0.316`.
''')
    rows=[]
    for name in ['SSO_SMA_TO_SP500','SSO_SMA_TO_TBILL','UPRO_SMA_TO_SP500','UPRO_SMA_TO_TBILL']:
        for l in ['LAG1','LAG2']:
            rows.append([name,l]+[pct(result(name,l,b).cagr) for b in (0,10,25,50)])
    body.append(table(['Strategy','Execution','0 bp','10 bp','25 bp','50 bp'],rows))
    body.append('''
All volatility, cash-excess Sharpe, underwater durations, switches, daily-entry worst 5/10/20/30-year CAGRs,
and monthly-entry 20/30-year cohort minima/medians are in the execution and cost CSVs. Percentage of benefit retained
is `100 × (W_delayed − W_always)/(W_immediate − W_always)`, holding costs fixed; log retention uses log wealth ratios.
It is undefined for always-on and may be negative. Finite overlapping cohorts are not independent observations.

## Non-overlapping periods

UPRO CAGR at zero switching cost and 50 bp financing spread:
''')
    rows=[]
    for period in ['1987_1999','2000_2009','2010_2019','2020_latest','2010_latest']:
        for l in ['LAG1','LAG2']:
            d=sub[(sub.fund=='UPRO')&(sub.period==period)&(sub.lag==l)].set_index('series')
            rows.append([period,l]+[pct(d.loc[n,'cagr']) for n in ['SP500_1X','UPRO_ALWAYS','UPRO_SMA_TO_SP500','UPRO_SMA_TO_TBILL']])
    body.append(table(['Period','Lag','1×','Always 3×','SMA → 1×','SMA → bills'],rows))
    body.append('''
The first four rows per lag partition 1987–latest; 2010–latest combines the last two, not another independent observation.
The longer three-block view reuses 1987–1999 and 2000–2009. CSVs retain both benchmark wealth ratios,
terminal multiples, volatility, Sharpe and drawdowns for SSO and secondary TQQQ too. Returns ending inside each
interval are included; the immediately preceding close supplies initial capital. Signals never restart at boundaries.
Early Nasdaq results retain the pre-1999 price-only proxy limitation.
''')
    # Quantify whether the result exists outside the exceptional lost decade.
    for l in ['LAG1','LAG2']:
        r=primary[('UPRO_SMA_TO_SP500',int(l[-1]),0)]
        a=primary[('UPRO_ALWAYS',int(l[-1]),0)]
        delta=np.log1p(r)-np.log1p(a)
        bear=delta.loc['2000':'2009'].sum()
        body.append(f'{l}: 2000–2009 contributes **{bear:.3f} log units** versus {delta.sum():.3f} for the entire matched history; '
                    f'the rest contributes {delta.sum()-bear:+.3f}. This concentration limits the broad robustness claim.\n')
    body.append('## Price versus total-return SMA\n')
    rows=[]
    for l in ['LAG1','LAG2']:
        for source in ['TOTAL_RETURN_SMA','PRICE_SMA']:
            d=signal[(signal.series=='UPRO_SMA_TO_SP500')&(signal.lag==l)&(signal.source==source)].iloc[0]
            rows.append([l,source,d.entry_close,pct(d.cagr),pct(d.max_drawdown),num(d.terminal_multiple),pct(d.cohort_20y_min_cagr),pct(d.cohort_30y_min_cagr)])
    body.append(table(['Lag','Signal','Entry close','CAGR','Max DD','Wealth','20y min','30y min'],rows))
    body.append('''
Only the SMA input changes: Yahoo S&P 500 `^GSPC` / Nasdaq-100 `^NDX` close prices versus archived underlying TR levels.
Sources are frozen with URLs and SHA-256 in `sma_price_input_manifest.json`; no open/intraday data were introduced.
The observed-TR-only S&P test begins after a full 200-close post-1988 warm-up; Nasdaq after a post-March-1999 warm-up.
All source/lag comparisons within a family use identical dates. Nasdaq lacks a 30-year eligible cohort, so it is blank,
not zero. `differing_switch_dates` counts dates on which one source switches and the other does not, distinct from the
net difference in switch counts. A smaller signal-disagreement fraction does not guarantee small performance differences.
''')
    full=all_signal[(all_signal.signal_window=='full_archive_with_proxy')&(all_signal.series=='UPRO_SMA_TO_SP500')]
    body.append('The companion full-archive S&P test includes 1987, retaining the original VFINX TR proxy before 1988:')
    body.append(table(['Lag','Signal','CAGR','Max DD','Wealth'],[
        [d.lag,d.source,pct(d.cagr),pct(d.max_drawdown),num(d.terminal_multiple)] for d in full.itertuples()]))
    body.append('The early invested-return proxy is unchanged. In 1987, price crosses on **October 15** and the existing TR proxy on **October 16**. The earlier price signal lets LAG2 exit before Black Monday. Price-SMA LAG2 improves CAGR relative to its own LAG1, so delay is not uniformly pessimistic. Before 1988 this is also a price-index-versus-fund-proxy comparison, not an isolated dividend experiment. The numerical execution penalty is therefore fragile to signal construction; the clean post-1988 comparison remains separate.')
    body.append('## Volatility and attribution\n')
    rows=[]
    for d in vol.itertuples():
        rows.append([d.window,d.state,pct(d.mean_realized_vol),pct(d.median_realized_vol),
                     f'{pct(d.p25_realized_vol)} / {pct(d.p75_realized_vol)}',pct(d.share_highest_decile_days)])
    body.append(table(['Trailing sessions','Prior SMA state','Mean vol','Median vol','25th / 75th','Share of top-decile days'],rows))
    body.append('''
Volatility is sample SD × √252 using returns through the prior close; today's return cannot change today's state or estimate.
The highest-volatility decile is a full-sample descriptive cutoff, not a trading threshold. Approximate UPRO path drag
(`3 × r²`, annualized by 252) is about 5.4% above versus 27.0% below SMA; exact log path drag is 5.5% versus 28.9%.
Annualized conditional financing log drag is about 7.8% above versus 7.3% below: higher financing rates are **not**
what distinguishes below-SMA days. Staying 1× saves borrowing costs anyway. Conditional arithmetic means remain positive:
S&P about 0.050% above / 0.046% below per day; UPRO 0.116% / 0.105%. UPRO log means are 0.083% / −0.064%.
These are conditional descriptions, not forecasts or annual holding-period returns.

Exact UPRO advantage versus always-on, contributed on executed risk-off days (cumulative natural-log units, before switching costs):
''')
    rows=[]
    for d in attr[(attr.series.str.startswith('UPRO'))&(attr.state=='below')].itertuples():
        rows.append([d.series.replace('UPRO_SMA_TO_',''),d.lag,f'{d.path_compounding_improvement_log:+.3f}',
                     f'{d.financing_savings_log:+.3f}',f'{d.expense_savings_log:+.3f}',
                     f'{d.net_equity_exposure_change_log:+.3f}',f'{d.tbill_log_earned:+.3f}',f'{d.actual_log_advantage:+.3f}'])
    body.append(table(['Risk-off','Lag','Path saved','Funding saved','Fees saved','Equity exposure change','Bills earned','Total'],rows))
    body.append('''
Identity: `log(1+LETF) = L log(1+r) − path_drag − financing_drag − expense_drag`.
Financing is inferred from the unchanged synthetic daily-return accounting, including funding plus spread; the known
expense accrual is separated. Financing is removed before expense in the exact log decomposition. Path is an algebraic
compounding gap, not a paid fee or an independently identified causal effect.

For LAG1 UPRO → 1×, avoiding 3× equity contributes −1.820 log units, retaining 1× contributes +0.607,
and the net equity-exposure contribution is −1.214. Reduced leverage forgoes **net positive** underlying growth;
it wins because path (+2.519), financing (+0.633) and expense (+0.078) savings are larger.
UPRO → bills also forgoes the +0.607 of 1× equity, earning +0.248 in bills instead.
The CSV splits avoided negative-equity days from forgone positive-equity days, but those large gross contributions
must be netted; percentages of a small net benefit are misleading. Above-SMA relative contribution is exactly zero.
Actual-minus-explained residuals are at floating-point precision. Retained historical equity growth is not an estimate
of an expected future equity risk premium.
''')
    body.append('## Fixed volatility-rule comparators\n')
    rows=[]
    for name in ['SP500_1X','UPRO_ALWAYS','UPRO_SMA_TO_SP500','UPRO_SMA_TO_TBILL','VOL_TARGET_20','VOL_BINARY']:
        d=alternatives[(alternatives.series==name)&(alternatives.lag=='LAG1')&(alternatives.switch_cost_bps==0)].iloc[0]
        c=alternatives[(alternatives.series==name)&(alternatives.lag=='LAG1')&(alternatives.switch_cost_bps==25)].iloc[0]
        rows.append([name,pct(d.cagr),pct(c.cagr),pct(d.max_drawdown),f'{d.average_equity_exposure:.2f}×',int(d.switch_count)])
    body.append(table(['Strategy','CAGR 0bp','CAGR 25bp','Max DD 0bp','Mean exposure','Switches'],rows))
    binary=alternatives[(alternatives.series=='VOL_BINARY')&(alternatives.lag=='LAG1')&(alternatives.switch_cost_bps==0)].iloc[0]
    retained=np.log(binary.terminal_multiple/ua.terminal_multiple)/np.log(u1.terminal_multiple/ua.terminal_multiple)
    body.append(f'''
The discrete rule maps `clip(20% / prior-20-day vol, 1, 3)` to 1× below 1.5, 2× from 1.5 to below 2.5, and 3× otherwise.
It is an exposure bucket approximation, so realized portfolio volatility need not equal 20%. Binary uses UPRO below
20% realized vol and 1× otherwise. Both are lagged and neither threshold nor lookback was searched.
Binary reproduces {retained:.0%} of LAG1 SMA → 1×'s **excess log wealth**, with comparable average exposure,
but its drawdown is much worse. The target's low exposure and frequent switches confound mechanism comparisons;
its results cannot alone show that volatility filtering explains or refutes SMA's benefit.

## Stress events and delayed exits

UPRO → 1×, wealth rebased to $1 at the close preceding each episode. “Loss before signal” is from the highest
portfolio value within the episode through the first new below-SMA crossing; it includes losses on the signal day.
''')
    rows=[]
    for event in ['1987','2000_2002','2007_2009','2020','2022']:
        a=stress[(stress.series=='UPRO_SMA_TO_SP500')&(stress.event==event)&(stress.lag=='LAG1')].iloc[0]
        b=stress[(stress.series=='UPRO_SMA_TO_SP500')&(stress.event==event)&(stress.lag=='LAG2')].iloc[0]
        d=delay[(delay.series=='UPRO_SMA_TO_SP500')&(delay.event==event)].sort_values('additional_loss_relative_wealth').iloc[-1]
        rows.append([event,a.first_below_sma_signal_close,pct(a.decline_before_signal),pct(a.episode_max_drawdown),
                     pct(b.episode_max_drawdown),f'{pct(d.additional_loss_relative_wealth)} ({d.signal_close})'])
    body.append(table(['Episode','First sell signal','Loss before signal','DD L1','DD L2','Worst single-exit delay loss (signal)'],rows))
    body.append('''
**1987 dominates the execution warning:** the Friday October 16 signal precedes Black Monday. Waiting through Monday
reduces wealth by another 51.6% relative to immediate UPRO → 1× exit over that session. The portfolio had already lost
43.4% from its episode peak before the signal. This uses the original pre-1988 grossed VFINX proxy and is especially
sensitive to its crash-date accuracy. No crash avoidance should be assumed before a signal exists.
Delay is not uniformly worse: 2020 episode drawdown and ending wealth improve here with LAG2 because of the full
sequence of delayed buy and sell decisions, despite adverse individual delayed exits.

`stress_event_detail` reports every S&P rotation and always-on comparator, signal/actual execution/first affected return dates,
value at signal, subsequent minimum, first re-entry, all switches, episode end and eventual recovery.
A whipsaw is explicitly a reversal within 10 sessions of the preceding switch; all switches remain separately reported.
First re-entry may be brief and outside the episode. Already-below-at-start is flagged; it is not invented as a fresh sell signal.
Always-on signal fields are reference SMA crossings, not trades. Recovery follows the episode's maximum-drawdown trough
back to its associated episode peak, including history after the episode. Blank means not observed, not instant recovery.
The companion transition CSV lists **every** sell signal within each stress episode and its next-session loss; positive
values mean waiting hurt, negative values mean it helped. These single-exit effects are not added as if independent.

## Robustness classification
''')
    rows=[]
    for fund in ['SSO','UPRO']:
        g=grid[grid.series.str.startswith(fund)].pivot(index=['sma_days','spread_bps','lag','switch_cost_bps'],columns='series',values='cagr')
        a=g[f'{fund}_SMA_TO_SP500']-g[f'{fund}_ALWAYS']
        b=g[f'{fund}_SMA_TO_SP500']-g[f'{fund}_SMA_TO_TBILL']
        rows.append([f'{fund} → 1× beats always-on',f'{int((a>0).sum())}/{len(a)}',pct(a.min()),pct(a.max())])
        rows.append([f'{fund} → 1× beats bills',f'{int((b>0).sum())}/{len(b)}',pct(b.min()),pct(b.max())])
    body.append(table(['Full-history CAGR claim','Positive grid cells','Min CAGR gap','Max CAGR gap'],rows))
    body.append('''
Grid = 150/200/250 sessions × 0/50/100 bp financing × LAG1/LAG2 × 0/10/25/50 bp switching costs.
The 72 cells per comparison reuse one history and are **not** 72 independent confirmations.

- **Robust descriptive association:** below-SMA states have higher prior realized volatility at both specified windows.
  This does not assert a robust causal strategy edge.
- **Moderately robust, conditional:** UPRO → 1× improves full-history wealth under either lag with 0–25 bp switching cost
  across tested lengths/spreads. Magnitude depends materially on timing, and it loses in some historical regimes.
- **Fragile as a universal outperformance claim:** joint delay/50 bp tests reverse UPRO's advantage; SSO reverses even
  under milder friction. Both UPRO lag versions underperform always-on over 2010–latest.
- **Moderately robust full-history 1×-over-bills ranking; fragile universal ranking:** all grid cells favor 1×,
  but 2000–2009 favors bills, and some downside/cohort criteria differ. Do not call it dominance.
- **Moderately supported mechanism, not identified:** log attribution and binary volatility results are consistent
  with volatility/path filtering. Expected-return changes, average leverage and financing also matter.

**Leading candidate for further study: UPRO above trend, 1× S&P below trend, with a downgraded robustness claim.**
SSO is not automatically a safer version of the same growth result; its smaller edge is easier to erase with friction.
TQQQ remains secondary: early Nasdaq is price-only and even rotated drawdowns are extreme.
The original held-out validation concerns synthetic ETF reconstruction, not out-of-sample timing performance.
Future research needs genuinely unseen outcomes and an exposure-matched counterfactual; this battery supplies neither.

## Reproduction and outputs

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m letf.falsification --offline
```

Original histories/DTB3 restore from the existing verified input bundle; price signals restore from the new separate
verified bundle. No original history, raw-source manifest or validation result is rewritten. Online mode only caches
price-signal sources; original histories still use verified offline inputs. Data endpoint is fixed by config.
Full metrics are in the eight requested CSVs, plus `sma_falsification_grid.csv`, `sma_stress_delay_transitions.csv`
and provenance manifests. Daily strategy returns and positions are reproducibly generated under `data/processed/`.
Numbers are decimal returns, wealth multiples, natural-log units or explicitly named basis points; blanks denote
unavailable horizons/dates. CSV row metadata gives exact coverage. No funding, threshold, weighting or SMA optimization.

![UPRO strategy wealth](sma_falsification_wealth.png)

![Matching drawdowns](sma_falsification_drawdown.png)

![Prior realized volatility distribution](sma_falsification_volatility.png)

![Non-overlapping subperiod CAGR](sma_falsification_subperiods.png)
''')
    (reports/'sma_falsification_results.md').write_text('\n\n'.join(body)+'\n')
    plot(reports,primary,underlying,state_vol,calendar,sub)


def plot(reports,primary,underlying,state_vol,calendar,sub):
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    names=['UPRO_ALWAYS','UPRO_SMA_TO_SP500','UPRO_SMA_TO_TBILL','VOL_TARGET_20']
    labels=['Always UPRO','SMA → 1×','SMA → bills','Vol target 20%']
    colors=['#9f4a48','#167e74','#4a64a0','#bb8a25','#68707b']
    for kind in ['wealth','drawdown']:
        fig,ax=plt.subplots(figsize=(10,5),layout='constrained')
        for name,label,color in zip(names+['SP500_1X'],labels+['S&P 500 1×'],colors):
            r=underlying if name=='SP500_1X' else primary[(name,1,0)]
            nav=path(r,calendar[calendar.get_loc(r.index[0])-1])
            ax.plot(nav.index,nav if kind=='wealth' else nav/nav.cummax()-1,label=label,color=color,lw=1.1)
        if kind=='wealth':
            ax.set_yscale('log'); ax.set_ylabel('Wealth per $1 (log scale)')
        else:
            ax.yaxis.set_major_formatter(PercentFormatter(1));ax.set_ylabel('Drawdown from prior peak')
        ax.set_title('S&P strategy comparison · LAG1, zero switching cost, 50 bp funding spread')
        ax.legend(ncols=3,frameon=False,fontsize=9);ax.grid(alpha=.15)
        fig.savefig(reports/f'sma_falsification_{kind}.png',dpi=150);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(10,4),layout='constrained',sharey=True)
    for ax,window in zip(axes,[20,60]):
        d=state_vol[window]
        for state,label,color in [(1,'Above SMA','#167e74'),(0,'Below SMA','#9f4a48')]:
            values=np.sort(d.loc[d.state==state,'vol'])
            ax.plot(values,np.arange(1,len(values)+1)/len(values),label=label,color=color)
        ax.set_title(f'Prior {window}-session realized volatility');ax.set_xlabel('Annualized volatility')
        ax.xaxis.set_major_formatter(PercentFormatter(1));ax.yaxis.set_major_formatter(PercentFormatter(1))
        ax.grid(alpha=.15);ax.legend(frameon=False)
    axes[0].set_ylabel('Cumulative share of state observations')
    fig.savefig(reports/'sma_falsification_volatility.png',dpi=150);plt.close(fig)
    fig,axes=plt.subplots(2,1,figsize=(10,7),layout='constrained',sharex=True,sharey=True)
    periods=['1987_1999','2000_2009','2010_2019','2020_latest']
    for ax,l in zip(axes,['LAG1','LAG2']):
        for i,(name,label,color) in enumerate(zip(names[:3],labels[:3],colors[:3])):
            vals=[sub[(sub.fund=='UPRO')&(sub.lag==l)&(sub.period==p)&(sub.series==name)].cagr.iloc[0] for p in periods]
            ax.bar(np.arange(4)+(i-1)*.24,vals,width=.23,label=label,color=color)
        ax.axhline(0,color='#444444',lw=.6);ax.yaxis.set_major_formatter(PercentFormatter(1))
        ax.set_ylabel('CAGR');ax.set_title(l+' · zero switching cost');ax.grid(axis='y',alpha=.15)
    axes[0].legend(ncols=3,frameon=False);axes[1].set_xticks(np.arange(4),['1987–1999','2000–2009','2010–2019','2020–latest'])
    fig.savefig(reports/'sma_falsification_subperiods.png',dpi=150);plt.close(fig)
