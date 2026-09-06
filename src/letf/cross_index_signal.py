"""Cross-index leverage timing: use S&P 500 price SMA to govern TQQQ exposure."""
from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np
import pandas as pd

from .analysis import CASH, load_inputs, matched
from .falsification import COSTS, LAGS, PERIODS, evaluate, level_position, load_price_signals, select_returns, subperiod_index, switching_costs
from .price_signal_revision import SMA_LENGTHS, SPREADS


def cohort_percentiles(r: pd.Series, calendar: pd.DatetimeIndex, horizons=(10,20,30)):
    from .analysis import path
    nav = path(r, calendar[calendar.get_loc(r.index[0])-1])
    monthly = ~nav.index.to_period('M').duplicated(keep='last')
    rows=[]
    for years in horizons:
        ends = nav.index.searchsorted(nav.index + pd.DateOffset(years=years))
        starts = np.flatnonzero((ends < len(nav)) & monthly)
        finishes = ends[starts]
        elapsed=(nav.index[finishes]-nav.index[starts]).days.to_numpy()
        cagr=(nav.to_numpy()[finishes]/nav.to_numpy()[starts])**(365.25/elapsed)-1
        q=np.quantile(cagr,[.01,.10,.25,.50,.75,.90,.99]) if len(cagr) else [np.nan]*7
        rows.append(dict(horizon_years=years,cohorts=len(cagr),p1=q[0],p10=q[1],p25=q[2],p50=q[3],p75=q[4],p90=q[5],p99=q[6]))
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
    pd.DataFrame(rows).to_csv(out/'cross_index_tqqq_sma_grid.csv',index=False,float_format='%.12g')
    pd.DataFrame(subs).to_csv(out/'cross_index_tqqq_sma_subperiods.csv',index=False,float_format='%.12g')
    pd.DataFrame(cohorts).to_csv(out/'cross_index_tqqq_sma_cohorts.csv',index=False,float_format='%.12g')
    pd.DataFrame(agreements).to_csv(out/'cross_index_tqqq_signal_agreement.csv',index=False,float_format='%.12g')

    df=pd.DataFrame(rows)
    pri=df[(df.sma_days==200)&(df.spread_bps==50)&(df.switch_cost_bps.isin([0,25]))&df.series.str.contains('TQQQ_')]
    def p(x): return f'{100*x:.2f}%'
    lines=['# TQQQ governed by S&P 500 price SMA','',
           'TQQQ/QQQ or TQQQ/T-bill rotation using the S&P 500 price-index SMA as the common equity-risk signal. Investment returns remain total-return based. No parameters were optimized.','',
           '## Primary 200-day / 50-bp financing results','',
           '| Strategy | Lag | Cost | CAGR | Max DD | Sharpe | Leveraged days | Switches/yr |','|---|---|---:|---:|---:|---:|---:|---:|']
    for _,r in pri.sort_values(['switch_cost_bps','lag','series']).iterrows():
        lines.append(f"| {r.series} | {r.lag} | {int(r.switch_cost_bps)} bp | {p(r.cagr)} | {p(r.max_drawdown)} | {r.sharpe:.2f} | {p(r.fraction_days_leveraged)} | {r.switches_per_year:.2f} |")
    lines += ['', '## Interpretation','',
              'Compare the S&P-signal variants directly with the otherwise identical Nasdaq-100-signal variants. The S&P rule is favored only if its advantage survives SMA-length, lag, switching-cost, financing-spread, subperiod, and long-cohort checks; no full-history winner should be treated as an optimized rule.']
    (out/'cross_index_tqqq_sma_results.md').write_text('\n'.join(lines)+'\n')


def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--root',type=Path,default=Path.cwd()); args=ap.parse_args(); run(args.root)

if __name__=='__main__': main()
