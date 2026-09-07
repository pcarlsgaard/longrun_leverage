"""Monthly fresh-investor cohorts: reset holdings/HWM/recovery state at entry."""
import argparse
import json
from pathlib import Path
import pandas as pd
from .analysis import CASH, load_inputs, matched, path, sha
from .falsification import load_price_signals
from .signals import level_position
from .capital_reserve import PRIMARY_RULES
from .reserve import simulate_reserve
from .provenance import FLOAT_FORMAT, source_hashes


def run(root):
    daily,config=load_inputs(root,offline=True); calendar=daily.index
    prices=load_price_signals(root,config,offline=True)
    positions={lag:level_position(prices['SP500'],calendar,200,lag) for lag in (1,2)}
    common=matched(pd.concat([daily[['SP500_1X','NASDAQ100_1X','LONG_TREASURY_1X',CASH]],
        level_position(prices['SP500'],calendar,250,2),
        level_position(prices['NASDAQ100'],calendar,250,2)],axis=1)).index
    prior=calendar[calendar.get_loc(common[0])-1]
    closes=pd.DatetimeIndex([prior]).append(common)
    entries=closes[~closes.to_period('M').duplicated(keep='last')]
    entries=entries[entries+pd.DateOffset(years=20)<=common[-1]]
    u=path(daily.SP500_1X.dropna(),calendar[0]); dd=u/u.cummax()-1
    rows=[]
    # 0 and moderate 25bp are prespecified fresh-state checks; every lag/fund/rule.
    # Full continuous-policy cost grid remains 0/10/25/50bp.
    for fund in ('UPRO','SSO'):
        for lag in (1,2):
            p=positions[lag]
            risky=daily[f'{fund}_BASE'].where(p.eq(1),daily.SP500_1X)
            for cost in (0,25):
                print(f'Fresh monthly cohorts: {fund} LAG{lag} {cost}bp',flush=True)
                for entry in entries:
                    start=calendar.get_loc(entry)+1
                    targets={y:calendar.searchsorted(entry+pd.DateOffset(years=y)) for y in (20,30)}
                    valid={y:j for y,j in targets.items() if j<len(calendar)}
                    stop=max(valid.values())+1
                    ix=calendar[start:stop]
                    for name,rule in PRIMARY_RULES.items():
                        nav=simulate_reserve(risky.loc[ix],daily.loc[ix,CASH],p.loc[ix],
                            dd.shift(lag).loc[ix],calendar,rule,3 if fund=='UPRO' else 2,lag,cost,ledger=False)
                        for y,j in valid.items():
                            value=nav.loc[calendar[j]]
                            rows.append(dict(series=name,fund=fund,lag=f'LAG{lag}',cost_bps=cost,
                                horizon_years=y,entry_close=entry,exit_close=calendar[j],
                                multiple=value,cagr=value**(365.25/(calendar[j]-entry).days)-1))
    f=pd.DataFrame(rows)
    f.to_csv(root/'data/processed/capital_reserve_fresh_cohorts.csv',index=False,float_format=FLOAT_FORMAT)
    return summarize(root,f)


def summarize(root,f):
    keys=['fund','lag','cost_bps','horizon_years','entry_close','exit_close']
    baseline=f[f.series=='NO_RESERVE'][keys+['multiple']].rename(columns={'multiple':'no_reserve_multiple'})
    f=f.merge(baseline,on=keys,validate='many_to_one')
    f['ratio_vs_no_reserve']=f.multiple/f.no_reserve_multiple
    f['outperformed_no_reserve']=f.ratio_vs_no_reserve>1+1e-10
    result=f.groupby(['series','fund','lag','cost_bps','horizon_years']).agg(
        cohorts=('multiple','size'),first_entry=('entry_close','min'),last_entry=('entry_close','max'),
        min_terminal_multiple=('multiple','min'),median_terminal_multiple=('multiple','median'),
        min_cagr=('cagr','min'),median_cagr=('cagr','median'),
        min_ratio_vs_no_reserve=('ratio_vs_no_reserve','min'),
        median_ratio_vs_no_reserve=('ratio_vs_no_reserve','median'),
        max_ratio_vs_no_reserve=('ratio_vs_no_reserve','max'),
        fraction_outperforming_no_reserve=('outperformed_no_reserve','mean')).reset_index()
    result['cohort_kind']='fresh_investor_reset'
    result.to_csv(root/'reports/capital_reserve_fresh_rolling_summary.csv',index=False,float_format=FLOAT_FORMAT)
    manifest=dict(cohort_kind='fresh_investor_reset',cost_bps=[0,25],lags=[1,2],
        funds=['UPRO','SSO'],horizons=[20,30],cohort_rows=len(f),
        summary_sha256=sha(root/'reports/capital_reserve_fresh_rolling_summary.csv'),
        input_bundle_sha256=sha(root/'data/snapshots/portfolio_sma_inputs.zip'),
        config_sha256=sha(root/'config.json'),
        source_hashes=source_hashes(root, __name__))
    (root/'reports/capital_reserve_fresh_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    from .reserve_report import refresh_report
    if (root/'reports/capital_reserve_manifest.json').exists():
        refresh_report(root)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path.cwd())
    run(parser.parse_args().root)
