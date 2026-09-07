"""Cross-index leverage timing: use S&P 500 price SMA to govern TQQQ exposure."""
from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np
import pandas as pd

from .analysis import CASH, load_inputs, matched
from .cohorts import cohort_quantiles, nav_path
from .falsification import COSTS, LAGS, PERIODS, evaluate, level_position, load_price_signals, select_returns, subperiod_index, switching_costs
from .price_signal_revision import SMA_LENGTHS, SPREADS
from .provenance import FLOAT_FORMAT

PERCENTILES = (.01, .10, .25, .50, .75, .90, .99)


def cohort_percentiles(r: pd.Series, calendar: pd.DatetimeIndex, horizons=(10,20,30)):
    """Month-end cohort CAGR percentiles; see :mod:`letf.cohorts`."""
    nav = nav_path(r, calendar)
    rows=[]
    for years in horizons:
        cagr,q = cohort_quantiles(nav, years, PERCENTILES)
        rows.append(dict(horizon_years=years,cohorts=len(cagr),
                         p1=q[0],p10=q[1],p25=q[2],p50=q[3],p75=q[4],p90=q[5],p99=q[6]))
    return rows


def run(root: Path):
    daily, config = load_inputs(root, offline=True)
    calendar, cash = daily.index, daily[CASH]
    prices = load_price_signals(root, config, offline=True)

    sp_pos={(n,lag): level_position(prices['SP500'],calendar,n,lag) for n in SMA_LENGTHS for lag in LAGS}
    ndx_pos={(n,lag): level_position(prices['NASDAQ100'],calendar,n,lag) for n in SMA_LENGTHS for lag in LAGS}
    ix=matched(pd.concat([daily[['NASDAQ100_1X',CASH]],sp_pos[(250,2)],ndx_pos[(250,2)]],axis=1)).index

    rows=[]; subs=[]; cohorts=[]; agreements=[]
    strategies={
        'TQQQ_SP500_SMA_TO_NASDAQ':'NASDAQ100_1X',
        'TQQQ_SP500_SMA_TO_TBILL':CASH,
        'TQQQ_NDX_SMA_TO_NASDAQ':'NASDAQ100_1X',
        'TQQQ_NDX_SMA_TO_TBILL':CASH,
    }
    for n in SMA_LENGTHS:
        for lag in LAGS:
            sp=sp_pos[(n,lag)].loc[ix]; ndx=ndx_pos[(n,lag)].loc[ix]
            agreements.append(dict(sma_days=n,lag=f'LAG{lag}',state_agreement_fraction=sp.eq(ndx).mean(),
                                   differing_transition_dates=int((sp.ne(sp.shift(1))).ne(ndx.ne(ndx.shift(1))).sum())))
            for spread in SPREADS:
                for cost in COSTS:
                    for name,off in strategies.items():
                        p = sp if '_SP500_' in name else ndx
                        gross=select_returns(daily,p,{0:off,1:f'TQQQ_SPREAD_{spread}BP'})
                        net=switching_costs(gross,p,cost)
                        m=evaluate(net,cash,calendar,p,cohorts=True)
                        rows.append(dict(series=name,signal_index='SP500' if '_SP500_' in name else 'NASDAQ100',
                                         sma_days=n,spread_bps=spread,lag=f'LAG{lag}',execution=LAGS[lag],
                                         switch_cost_bps=cost,fraction_days_leveraged=p.mean(),**m))
                        if n==200 and spread==50 and cost in (0,25):
                            for period,(start,end) in PERIODS.items():
                                if period=='2010_latest': continue
                                si=subperiod_index(ix,start,end)
                                sm=evaluate(net.loc[si],cash,calendar,p.loc[si],cohorts=False)
                                subs.append(dict(series=name,signal_index='SP500' if '_SP500_' in name else 'NASDAQ100',
                                                 lag=f'LAG{lag}',switch_cost_bps=cost,period=period,**sm))
                            for c in cohort_percentiles(net,calendar):
                                cohorts.append(dict(series=name,signal_index='SP500' if '_SP500_' in name else 'NASDAQ100',
                                                    sma_days=n,spread_bps=spread,lag=f'LAG{lag}',switch_cost_bps=cost,**c))

    # Baselines for primary comparison only.
    for name,col in [('NASDAQ100_1X','NASDAQ100_1X'),('TQQQ_ALWAYS','TQQQ_SPREAD_50BP')]:
        r=daily.loc[ix,col]
        m=evaluate(r,cash,calendar,None,cohorts=True)
        rows.append(dict(series=name,signal_index='NONE',sma_days=200,spread_bps=50,lag='LAG1',execution='NA',switch_cost_bps=0,
                         fraction_days_leveraged=1.0 if name=='TQQQ_ALWAYS' else 0.0,**m))
        for c in cohort_percentiles(r,calendar):
            cohorts.append(dict(series=name,signal_index='NONE',sma_days=200,spread_bps=50,lag='LAG1',switch_cost_bps=0,**c))

    out=root/'reports'
    pd.DataFrame(rows).to_csv(out/'cross_index_tqqq_sma_grid.csv',index=False,float_format=FLOAT_FORMAT)
    pd.DataFrame(subs).to_csv(out/'cross_index_tqqq_sma_subperiods.csv',index=False,float_format=FLOAT_FORMAT)
    pd.DataFrame(cohorts).to_csv(out/'cross_index_tqqq_sma_cohorts.csv',index=False,float_format=FLOAT_FORMAT)
    pd.DataFrame(agreements).to_csv(out/'cross_index_tqqq_signal_agreement.csv',index=False,float_format=FLOAT_FORMAT)

    report(root, pd.DataFrame(rows), pd.DataFrame(subs), pd.DataFrame(cohorts),
           pd.DataFrame(agreements))
    print(f'Cross-index comparison: {len(rows)} grid rows, {ix[0].date()}-{ix[-1].date()}.')


def report(root, df, subs, cohorts, agreements):
    """Write the whole report, prose included.

    Nothing in the committed file may be hand-added: this generator overwrites
    the path on every run, so an appended paragraph survives only until the next
    person runs the documented command. Analysis that belongs in the report is
    computed here.
    """
    def p(x): return f'{100 * x:.2f}%'
    pri = df[(df.sma_days == 200) & (df.spread_bps == 50)
             & (df.switch_cost_bps.isin([0, 25])) & df.series.str.contains('TQQQ_')]

    # Paired comparison: identical off-sleeve, SMA length, spread, lag and cost,
    # differing only in which index supplies the signal.
    keys = ['sma_days', 'spread_bps', 'lag', 'switch_cost_bps']
    wins, total, by_length = 0, 0, {}
    for off in ('NASDAQ', 'TBILL'):
        a = df[df.series == f'TQQQ_SP500_SMA_TO_{off}'].set_index(keys).cagr
        b = df[df.series == f'TQQQ_NDX_SMA_TO_{off}'].set_index(keys).cagr
        pair = pd.concat([a.rename('sp'), b.rename('ndx')], axis=1).dropna()
        wins += int((pair.sp > pair.ndx).sum()); total += len(pair)
        for n, group in pair.groupby(level='sma_days'):
            won, seen = by_length.get(n, (0, 0))
            by_length[n] = (won + int((group.sp > group.ndx).sum()), seen + len(group))
    agree200 = agreements[(agreements.sma_days == 200) & (agreements.lag == 'LAG2')]
    agreement = float(agree200.state_agreement_fraction.iloc[0]) if len(agree200) else float('nan')

    lines = [
        '# TQQQ governed by the S&P 500 price SMA', '',
        'TQQQ/QQQ or TQQQ/T-bill rotation using the S&P 500 price-index SMA as a common',
        'equity-risk signal, compared against the otherwise identical rule driven by the',
        "Nasdaq-100's own SMA. Investment returns remain total-return based. No parameters",
        'were optimized.', '',
        '## Primary 200-day / 50-bp financing results', '',
        '| Strategy | Lag | Cost | CAGR | Max DD | Sharpe | Leveraged days | Switches/yr |',
        '|---|---|---:|---:|---:|---:|---:|---:|',
    ]
    for _, r in pri.sort_values(['switch_cost_bps', 'lag', 'series']).iterrows():
        lines.append(f'| {r.series} | {r.lag} | {int(r.switch_cost_bps)} bp | {p(r.cagr)} | '
                     f'{p(r.max_drawdown)} | {r.sharpe:.2f} | {p(r.fraction_days_leveraged)} | '
                     f'{r.switches_per_year:.2f} |')

    lines += [
        '', '## How many of these comparisons are independent?', '',
        f'Holding the off-sleeve, SMA length, financing spread, execution lag and switching',
        f'cost fixed and varying only the signal index gives {total} paired comparisons. The',
        f'S&P signal has the higher CAGR in {wins}/{total} ({p(wins / total)}) of them.', '',
        '**That fraction is not {n} pieces of evidence.**'.format(n=total),
        f'At 200 days the two signals hold the same state on {p(agreement)} of sessions, so',
        'these are one pair of highly correlated signal paths re-scored under nuisance',
        'parameters, over one history. The effective sample is one comparison. A win rate',
        'near 100% across a parameter grid tells you the result is insensitive to those',
        'parameters; it says nothing about how often the conclusion would hold on data',
        'this study has not seen.', '',
        '| SMA length | S&P signal wins | Comparisons |',
        '|---|---:|---:|',
    ]
    for n in sorted(by_length):
        won, seen = by_length[n]
        lines.append(f'| {n} days | {p(won / seen)} | {seen} |')

    lines += ['', '## The subperiod evidence, which is what actually varies', '']
    prim = subs[(subs.lag == 'LAG2') & (subs.switch_cost_bps == 25)] if len(subs) else subs
    if len(prim):
        table = prim.pivot_table(index='period', columns='series', values='cagr')
        cols = [c for c in ('TQQQ_NDX_SMA_TO_NASDAQ', 'TQQQ_SP500_SMA_TO_NASDAQ') if c in table]
        if len(cols) == 2:
            lines += ['LAG2, 25 bp, rotating to Nasdaq 1x:', '',
                      '| Period | Nasdaq signal | S&P signal | Difference |',
                      '|---|---:|---:|---:|']
            for period, row in table[cols].iterrows():
                diff = row[cols[1]] - row[cols[0]]
                lines.append(f'| {period} | {p(row[cols[0]])} | {p(row[cols[1]])} | '
                             f'{100 * diff:+.2f} pp |')
            lines += ['', 'The S&P signal is not uniformly superior. It wins strongly in the earliest and',
                      'the 2010s blocks, is roughly a tie in 2000-2009, and loses substantially in',
                      '2020-latest, when the Nasdaq trend carried useful asset-specific information.',
                      'Four blocks, split two-one-one, is the honest sample size behind the headline',
                      'win rate above.', '']

    lines += ['## Long-horizon cohort distributions: LAG2 / 25 bp', '']
    for years in (20, 30):
        block = cohorts[(cohorts.horizon_years == years) & (cohorts.lag == 'LAG2')
                        & (cohorts.switch_cost_bps == 25)] if len(cohorts) else cohorts
        if not len(block):
            continue
        lines += [f'### {years}-year CAGR', '',
                  '| Strategy | P1 | P10 | P25 | P50 | P75 | P90 | P99 |',
                  '|---|---:|---:|---:|---:|---:|---:|---:|']
        for _, r in block.sort_values('series').iterrows():
            lines.append(f'| {r.series} | ' + ' | '.join(
                p(r[f'p{q}']) for q in (1, 10, 25, 50, 75, 90, 99)) + ' |')
        lines.append('')
    lines += ['These cohorts overlap heavily. They are descriptive historical outcomes, not',
              'independent probability draws, and early Nasdaq history retains the existing',
              'proxy limitations.', '']

    lines += [
        '## Interpretation', '',
        'The experiment is consistent with leverage intensity being governable by a common',
        'broad-equity risk regime rather than requiring an asset-specific trend signal, and',
        'the same parsimonious signal governing both S&P and Nasdaq leverage is an',
        'attractive property if it holds. It is not established here.', '',
        'Two things stop that conclusion from being a finding. The 2020-latest block',
        'reverses in favor of the Nasdaq-specific signal, which is the falsification',
        'caveat. And the grid win rate is one comparison, not a hundred and forty-four.',
        '`letf.null_model` tests the underlying strategies against a matched-exposure',
        'random-timing null; read that before treating any CAGR gap here as an edge.', '',
        'For TQQQ specifically the off-sleeve choice is genuinely unresolved by full-history',
        'CAGR. Staying in Nasdaq 1x preserves more upside; T-bills materially improve',
        'drawdowns and the long-horizon lower tail. That makes the safe-asset version worth',
        'equal attention for TQQQ, despite the earlier S&P-family finding that remaining in',
        '1x equity was generally preferable.', '',
    ]
    (root / 'reports' / 'cross_index_tqqq_sma_results.md').write_text('\n'.join(lines) + '\n')


def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--root',type=Path,default=Path.cwd()); args=ap.parse_args(); run(args.root)

if __name__=='__main__': main()
