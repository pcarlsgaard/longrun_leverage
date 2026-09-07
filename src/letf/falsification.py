"""Prespecified SMA falsification battery; never rebuilds underlying returns."""
from __future__ import annotations

import argparse
import json
import platform
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .analysis import (CASH, FAMILIES, REGIMES, load_inputs, matched, sma_position,
                       extended_metrics, path, sha)
from .cohorts import cohort_cagrs, nav_path, worst_cagr
from .data import cached_source, yahoo
from .model import calendar_days
# Re-exported: signal construction lives in letf.signals, cost mechanics in letf.strategy.
from .signals import LAGS, discrete_exposure, level_position, sma_position, volatility_position
from .strategy import select_returns, switching_costs, transitions

COSTS = (0, 10, 25, 50)
PERIODS = {'1987_1999': ('1987-01-01', '1999-12-31'),
           '2000_2009': ('2000-01-01', '2009-12-31'),
           '2010_2019': ('2010-01-01', '2019-12-31'),
           '2020_latest': ('2020-01-01', None),
           '2010_latest': ('2010-01-01', None)}


def rolling_stats(r, calendar):
    """Worst daily-entry windows plus month-end cohort summaries.

    Daily-entry minima are deliberately separate from monthly cohort minima:
    the former is a worst-case bound, the latter a distribution over
    investable entry dates. Both come from :mod:`letf.cohorts`.
    """
    nav = nav_path(r, calendar)
    out = {}
    for years in (5, 10, 20, 30):
        out[f'worst_rolling_{years}y_cagr'] = worst_cagr(nav, years)
        if years in (20, 30):
            cohorts = cohort_cagrs(nav, years)
            out.update({f'cohort_{years}y_count': len(cohorts),
                        f'cohort_{years}y_min_cagr': float(cohorts.min()) if len(cohorts) else np.nan,
                        f'cohort_{years}y_median_cagr': float(np.median(cohorts)) if len(cohorts) else np.nan})
    return out


def evaluate(r, cash, calendar, position=None, cohorts=True):
    m = extended_metrics(r, cash.loc[r.index], calendar)
    if cohorts:
        m.update(rolling_stats(r, calendar))
    switches = int(transitions(position).sum()) if position is not None else 0
    m.update(switch_count=switches, switches_per_year=switches/m['years'])
    return m


def subperiod_index(index, start, end):
    """Select return-end dates inclusively, keeping preceding close as baseline."""
    return index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end or index[-1]))]


def economic_components(underlying, leveraged, days, leverage, expense):
    """Exact log identity, financing first then fee; no simulated series rebuilt.

    Infer the original charged funding+spread from the archived return identity.
    Path term is L*log(1+r)-log(1+L*r), distinct from financing and expense.
    """
    if not underlying.index.equals(leveraged.index) or not days.index.equals(underlying.index):
        raise ValueError('Economic calendars differ')
    fee = expense * days / 365
    finance = leverage * underlying - leveraged - fee
    ideal = leverage * underlying
    if ((1+ideal <= 0) | (1+leveraged <= 0) | (finance < -1e-9)).any():
        raise ValueError('Log attribution requires surviving, untruncated sleeves')
    return pd.DataFrame({
        'underlying_log': np.log1p(underlying),
        'leveraged_log': np.log1p(leveraged),
        'path_drag_log': leverage*np.log1p(underlying)-np.log1p(ideal),
        'financing_drag_log': np.log1p(ideal)-np.log1p(ideal-finance),
        'expense_drag_log': np.log1p(ideal-finance)-np.log1p(leveraged),
        'financing_simple': finance,
        'approx_path_drag': .5*leverage*(leverage-1)*underlying**2,
    })


def attribution(components, position, cash, leverage, off_kind):
    rows = []
    for state, label in [(1, 'above'), (0, 'below')]:
        c = components.loc[position.eq(state)]
        off = state == 0
        u = c.underlying_log.sum()
        retained = u if off and off_kind == '1x' else 0.
        avoided = -leverage*u if off else 0.
        bill = np.log1p(cash.loc[c.index]).sum() if off and off_kind == 'tbill' else 0.
        path_saved = c.path_drag_log.sum() if off else 0.
        funding_saved = c.financing_drag_log.sum() if off else 0.
        fee_saved = c.expense_drag_log.sum() if off else 0.
        actual = (retained+bill-c.leveraged_log.sum()) if off else 0.
        explained = retained+avoided+bill+path_saved+funding_saved+fee_saved
        rows.append({'state': label, 'days': len(c), 'actual_log_advantage': actual,
                     'avoided_leveraged_equity_log': avoided,
                     'retained_1x_equity_log': retained,
                     'net_equity_exposure_change_log': avoided+retained,
                     'avoided_negative_equity_log': -(leverage-(off_kind == '1x'))*c.underlying_log.clip(upper=0).sum() if off else 0.,
                     'forgone_positive_equity_log': -(leverage-(off_kind == '1x'))*c.underlying_log.clip(lower=0).sum() if off else 0.,
                     'equity_log_forgone_to_cash': u if off and off_kind == 'tbill' else 0.,
                     'tbill_log_earned': bill, 'path_compounding_improvement_log': path_saved,
                     'financing_savings_log': funding_saved, 'expense_savings_log': fee_saved,
                     'explained_log_advantage': explained, 'identity_residual': actual-explained})
    return rows


def stress_detail(r, position, raw_signal, calendar, start, end, lag, rotating=True):
    ix = subperiod_index(r.index, start, end)
    if len(ix) < 2:
        return None
    prior = calendar[calendar.get_loc(ix[0])-1]
    nav = path(r, calendar[calendar.get_loc(r.index[0])-1])
    nav = nav / nav.loc[prior]
    segment = nav.loc[prior:ix[-1]]
    signals = raw_signal.loc[ix]
    sell = raw_signal.eq(0) & raw_signal.shift(1).eq(1)
    sell_dates = ix[sell.loc[ix]]
    first = sell_dates[0] if len(sell_dates) else pd.NaT
    def date(d): return '' if pd.isna(d) else str(d.date())
    execution, first_return, resumed = pd.NaT, pd.NaT, pd.NaT
    value, already, low = np.nan, np.nan, np.nan
    if not pd.isna(first):
        loc = calendar.get_loc(first)
        execution = calendar[loc+lag-1] if rotating else pd.NaT
        first_return = calendar[loc+lag] if rotating else pd.NaT
        value = nav.loc[first]
        already = 1-value/segment.loc[:first].max()
        low = nav.loc[first:ix[-1]].min()
        buys = raw_signal.eq(1) & raw_signal.shift(1).eq(0)
        later = calendar[(calendar > first) & buys.reindex(calendar, fill_value=False)]
        if len(later) and rotating:
            j = calendar.get_loc(later[0])+lag-1
            resumed = calendar[j] if j < len(calendar) else pd.NaT
    trough = segment.div(segment.cummax()).idxmin()
    target = segment.loc[:trough].max()
    recovered = nav.loc[trough:][nav.loc[trough:] >= target]
    switches = int(transitions(position).reindex(ix).sum()) if rotating else 0
    return {'first_below_sma_signal_close': date(first),
            'already_below_at_episode_start': bool(signals.iloc[0] == 0),
            'first_deleveraging_execution_close': date(execution),
            'first_deleveraged_return_end': date(first_return),
            'value_at_first_signal': value, 'decline_before_signal': already,
            'lowest_value_after_first_signal': low,
            'leverage_resumed_execution_close': date(resumed),
            'switch_count': switches,
            'whipsaw_reversal_switches_within_10_sessions': sum(
                b-a <= 10 for a,b in zip(np.flatnonzero(transitions(position).reindex(ix)),
                                         np.flatnonzero(transitions(position).reindex(ix))[1:])),
            'episode_end_value': segment.iloc[-1], 'episode_min_value': segment.min(),
            'episode_max_drawdown': (segment/segment.cummax()-1).min(),
            'eventual_recovery_date': date(recovered.index[0] if len(recovered) else pd.NaT)}


def load_price_signals(root, config, offline):
    """Separate frozen source-only bundle; original manifests and returns untouched."""
    manifest_path = root/'reports/sma_price_input_manifest.json'
    bundle = root/'data/snapshots/sma_price_inputs.zip'
    files = ['data/raw/yahoo_GSPC.json', 'data/raw/yahoo_NDX.json']
    if offline:
        manifest = json.loads(manifest_path.read_text())
        if manifest['as_of'] != config['as_of'] or sha(bundle) != manifest['bundle_sha256']:
            raise ValueError('Price snapshot/configuration mismatch')
        with zipfile.ZipFile(bundle) as z:
            for rel in files:
                p = root/rel
                if not p.exists():
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(z.read(rel))
                if sha(p) != manifest['inputs_sha256'][rel]:
                    raise ValueError(f'Price input hash mismatch: {rel}')
    else:
        end = int((pd.Timestamp(config['as_of'], tz='UTC')+pd.Timedelta(days=1)).timestamp())
        records = []
        for symbol in ('GSPC', 'NDX'):
            url = f'https://query1.finance.yahoo.com/v8/finance/chart/%5E{symbol}?period1=0&period2={end}&interval=1d&events=div%2Csplits%2CcapitalGains'
            records.append(cached_source(root/'data/raw', (f'yahoo_{symbol}.json', url)))
        with zipfile.ZipFile(bundle, 'w') as z:
            for rel in files:
                info = zipfile.ZipInfo(rel, (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, (root/rel).read_bytes())
        manifest = {'as_of': config['as_of'], 'files': records,
                    'inputs_sha256': {rel: sha(root/rel) for rel in files},
                    'bundle_sha256': sha(bundle),
                    'purpose': 'Close price signal only; invested returns unchanged'}
        manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
    return {u: yahoo(root/'data/raw', symbol, config['as_of'])['close']
            for u, symbol in [('SP500', '^GSPC'), ('NASDAQ100', '^NDX')]}


def run(root, offline=True):
    daily, config = load_inputs(root, offline=True)
    calendar, cash = daily.index, daily[CASH]
    price = load_price_signals(root, config, offline)
    positions = {(u,n,lag): sma_position(daily[f'{u}_1X'], calendar, n).shift(lag-1)
                 for u in set(FAMILIES.values()) for n in (150,200,250) for lag in LAGS}
    # Preserve the previous common portfolio window, including the 250-day warmup,
    # then require LAG2 too. Both families share the exact same return dates.
    ix = matched(pd.concat([daily[['SP500_1X','NASDAQ100_1X','LONG_TREASURY_1X',CASH]],
                            positions[('SP500',250,2)], positions[('NASDAQ100',250,2)]], axis=1)).index
    reports, processed = root/'reports', root/'data/processed'
    full_rows, primary, primary_states = [], {}, {}
    def record(r, p, meta, cohorts=True):
        return {**meta, **evaluate(r, cash, calendar, p, cohorts)}
    for fund,u in FAMILIES.items():
        print(f'Robustness grid: {fund}', flush=True)
        for n in (150,200,250):
            for spread in (0,50,100):
                for lag in LAGS:
                    p = positions[(u,n,lag)].loc[ix]
                    lev = daily.loc[ix,f'{fund}_SPREAD_{spread}BP']
                    off_label = 'SP500' if u == 'SP500' else 'NASDAQ'
                    for cost in COSTS:
                        for label,column in [('ALWAYS',None),(f'SMA_TO_{off_label}',f'{u}_1X'),('SMA_TO_TBILL',CASH)]:
                            name = f'{fund}_{label}'
                            state = p if column else pd.Series(1.,index=ix)
                            gross = select_returns(daily,state,{0:column,1:f'{fund}_SPREAD_{spread}BP'}) if column else lev
                            net = switching_costs(gross,state,cost)
                            meta = dict(series=name,family=u,sma_days=n,spread_bps=spread,
                                        lag=f'LAG{lag}',execution=LAGS[lag],switch_cost_bps=cost)
                            row = record(net,state,meta)
                            full_rows.append(row)
                            if n==200 and spread==50:
                                key=(name,lag,cost)
                                primary[key],primary_states[key] = net,state
    grid = pd.DataFrame(full_rows)
    # Benefit retention uses excess terminal wealth above matched always-on wealth.
    # Also provide log-wealth retention, avoiding dependence on this one definition.
    base = grid[(grid.sma_days==200)&(grid.spread_bps==50)].copy()
    for i,row in base.iterrows():
        fund = row.series.split('_')[0]
        match = base[(base.series==row.series)&(base.lag=='LAG1')&(base.switch_cost_bps==row.switch_cost_bps)].iloc[0]
        always = base[(base.series==f'{fund}_ALWAYS')&(base.lag=='LAG1')&(base.switch_cost_bps==0)].iloc[0]
        w,w1,wa=row.terminal_multiple,match.terminal_multiple,always.terminal_multiple
        base.loc[i,'terminal_wealth_difference_vs_primary'] = w-w1
        base.loc[i,'terminal_wealth_ratio_vs_primary'] = w/w1
        base.loc[i,'benefit_terminal_wealth_retained_pct'] = 100*(w-wa)/(w1-wa) if abs(w1-wa)>1e-12 else np.nan
        base.loc[i,'benefit_log_wealth_retained_pct'] = 100*np.log(w/wa)/np.log(w1/wa) if abs(w1-wa)>1e-12 else np.nan
    grid.to_csv(reports/'sma_falsification_grid.csv',index=False,float_format='%.12g')
    base[base.switch_cost_bps==0].to_csv(reports/'sma_execution_lag.csv',index=False,float_format='%.12g')
    base.to_csv(reports/'sma_switching_costs.csv',index=False,float_format='%.12g')
    subrows=[]
    for fund,u in FAMILIES.items():
        names=[f'{u}_1X',f'{fund}_ALWAYS',f'{fund}_SMA_TO_'+('SP500' if u=='SP500' else 'NASDAQ'),f'{fund}_SMA_TO_TBILL']
        for lag in LAGS:
            for name in names:
                r= daily.loc[ix,name] if name.endswith('_1X') else primary[(name,lag,0)]
                for period,(start,end) in PERIODS.items():
                    si=subperiod_index(ix,start,end)
                    m=record(r.loc[si],None,dict(series=name,family=u,fund=fund,lag=f'LAG{lag}',period=period,
                             period_set='longer' if period=='2010_latest' else 'nonoverlapping',
                             early_nasdaq_price_proxy=u=='NASDAQ100' and si[0]<pd.Timestamp('1999-03-05')),cohorts=False)
                    m['ending_wealth_ratio_vs_always']=(1+r.loc[si]).prod()/(1+primary[(f'{fund}_ALWAYS',lag,0)].loc[si]).prod()
                    m['ending_wealth_ratio_vs_1x']=(1+r.loc[si]).prod()/(1+daily.loc[si,f'{u}_1X']).prod()
                    subrows.append(m)
    sub=pd.DataFrame(subrows)
    sub.to_csv(reports/'sma_subperiods.csv',index=False,float_format='%.12g')
    print('Signal source and volatility comparisons',flush=True)
    signalrows=[]
    for fund,u in FAMILIES.items():
        for lag in LAGS:
            pp=level_position(price[u],calendar,200,lag)
            tr=positions[(u,200,lag)]
            # Nasdaq comparison only when the complete 200-close window uses
            # observed TR history, never the pre-1999 price-only segment.
            tr_coverage = pd.Timestamp('1988-01-05') if u=='SP500' else pd.Timestamp('1999-03-05')
            cutoff=calendar[calendar.searchsorted(tr_coverage)+201]
            windows = {'observed_tr_only': max(ix[0],cutoff)}
            if u == 'SP500':
                windows['full_archive_with_proxy'] = ix[0]
            for window,start in windows.items():
                si=matched(pd.concat([pp.rename('price'),tr.rename('tr')],axis=1)).loc[start:ix[-1]].index
                mismatch=(pp.loc[si]!=tr.loc[si]).mean()
                tp,tq=transitions(pp).loc[si],transitions(tr).loc[si]
                for source,p in [('TOTAL_RETURN_SMA',tr.loc[si]),('PRICE_SMA',pp.loc[si])]:
                    raw_source=(tr if source=='TOTAL_RETURN_SMA' else pp).shift(-lag)
                    sell_1987=raw_source[(raw_source==0)&(raw_source.shift(1)==1)].loc['1987']
                    for label,off in [('SP500' if u=='SP500' else 'NASDAQ',f'{u}_1X'),('TBILL',CASH)]:
                        name=f'{fund}_SMA_TO_{label}'
                        r=select_returns(daily,p,{0:off,1:f'{fund}_BASE'})
                        signalrows.append(record(r,p,dict(series=name,family=u,lag=f'LAG{lag}',source=source,
                            signal_window=window, signal_disagreement_fraction=mismatch,
                            first_sell_signal_1987=str(sell_1987.index[0].date()) if len(sell_1987) else '',
                            differing_switch_dates=int(tp.ne(tq).sum()),
                            price_switch_count=int(tp.sum()),total_return_switch_count=int(tq.sum()),
                            switch_count_difference=int(tp.sum()-tq.sum()))))
    pd.DataFrame(signalrows).to_csv(reports/'sma_signal_source_comparison.csv',index=False,float_format='%.12g')
    volrows=[]
    state_vol={}
    days=calendar_days(ix,calendar[calendar.get_loc(ix[0])-1])
    comp=economic_components(daily.loc[ix,'SP500_1X'],daily.loc[ix,'UPRO_BASE'],days,3,config['funds']['UPRO']['expense'])
    for window in (20,60):
        vol=daily.SP500_1X.rolling(window).std(ddof=1).shift(1).loc[ix]*np.sqrt(252)
        state=positions[('SP500',200,1)].loc[ix]
        high=vol>=vol.quantile(.9)
        state_vol[window]=pd.DataFrame({'vol':vol,'state':state})
        for s,label in [(1,'above'),(0,'below')]:
            mask=state.eq(s)
            v=vol[mask]
            volrows.append(dict(window=window,state=label,days=int(mask.sum()),mean_realized_vol=v.mean(),
                median_realized_vol=v.median(),p25_realized_vol=v.quantile(.25),p75_realized_vol=v.quantile(.75),
                share_highest_decile_days=float((mask&high).sum()/high.sum()),
                fraction_state_days_in_highest_decile=float(high[mask].mean()),
                mean_sp500_daily_return=daily.loc[ix,'SP500_1X'][mask].mean(),
                mean_upro_daily_return=daily.loc[ix,'UPRO_BASE'][mask].mean(),
                mean_sp500_daily_log_return=comp.underlying_log[mask].mean(),
                mean_upro_daily_log_return=comp.leveraged_log[mask].mean(),
                annualized_approx_path_drag=252*comp.approx_path_drag[mask].mean(),
                annualized_exact_path_drag_log=252*comp.path_drag_log[mask].mean(),
                annualized_financing_drag_log=252*comp.financing_drag_log[mask].mean()))
    pd.DataFrame(volrows).to_csv(reports/'sma_volatility_state_analysis.csv',index=False,float_format='%.12g')
    volmetrics=[]
    for lag in LAGS:
        for binary in (False,True):
            name='VOL_BINARY' if binary else 'VOL_TARGET_20'
            p,_=volatility_position(daily.SP500_1X,lag=lag,binary=binary)
            p=p.loc[ix]
            gross=select_returns(daily,p,{1:'SP500_1X',2:'SSO_BASE',3:'UPRO_BASE'})
            for cost in COSTS:
                r=switching_costs(gross,p,cost)
                primary[(name,lag,cost)]=r
                primary_states[(name,lag,cost)]=p
                volmetrics.append(record(r,p,dict(series=name,lag=f'LAG{lag}',switch_cost_bps=cost,
                    average_equity_exposure=p.mean(),fraction_1x=p.eq(1).mean(),fraction_2x=p.eq(2).mean(),fraction_3x=p.eq(3).mean())))
    for lag in LAGS:
        for cost in COSTS:
            for name in ['SP500_1X','UPRO_ALWAYS','UPRO_SMA_TO_SP500','UPRO_SMA_TO_TBILL']:
                r=daily.loc[ix,name] if name=='SP500_1X' else primary[(name,lag,cost)]
                p=None if name=='SP500_1X' else primary_states[(name,lag,cost)]
                exposure=1. if p is None else float((p*3+(1-p)*(0 if name.endswith('TBILL') else 1)).mean())
                volmetrics.append(record(r,p,dict(series=name,lag=f'LAG{lag}',switch_cost_bps=cost,average_equity_exposure=exposure)))
    pd.DataFrame(volmetrics).to_csv(reports/'volatility_target_comparison.csv',index=False,float_format='%.12g')
    attr=[]
    for fund in ('SSO','UPRO'):
        spec=config['funds'][fund]
        c=economic_components(daily.loc[ix,'SP500_1X'],daily.loc[ix,f'{fund}_BASE'],days,spec['leverage'],spec['expense'])
        for lag in LAGS:
            for kind,label in [('1x','SP500'),('tbill','TBILL')]:
                for row in attribution(c,positions[('SP500',200,lag)].loc[ix],cash,spec['leverage'],kind):
                    attr.append(dict(series=f'{fund}_SMA_TO_{label}',lag=f'LAG{lag}',**row))
    pd.DataFrame(attr).to_csv(reports/'sma_attribution.csv',index=False,float_format='%.12g')
    stress,delay=[] ,[]
    raw=positions[('SP500',200,1)].shift(-1)
    for fund in ('SSO','UPRO'):
        for name in [f'{fund}_ALWAYS',f'{fund}_SMA_TO_SP500',f'{fund}_SMA_TO_TBILL']:
            for lag in LAGS:
                r,p=primary[(name,lag,0)],primary_states[(name,lag,0)]
                for event,(start,end) in REGIMES.items():
                    row=stress_detail(r,p,raw,calendar,start,end,lag,rotating=not name.endswith('ALWAYS'))
                    stress.append(dict(series=name,lag=f'LAG{lag}',event=event,**row))
            if name.endswith('ALWAYS'): continue
            off=CASH if name.endswith('TBILL') else 'SP500_1X'
            for event,(start,end) in REGIMES.items():
                signals=raw.eq(0)&raw.shift(1).eq(1)
                for signal in signals.loc[start:end][signals.loc[start:end]].index:
                    nextday=calendar[calendar.get_loc(signal)+1]
                    lev=daily.loc[nextday,f'{fund}_BASE']; safe=daily.loc[nextday,off]
                    delay.append(dict(series=name,event=event,signal_close=str(signal.date()),
                        delayed_execution_close=str(nextday.date()),
                        waiting_session_leveraged_return=lev,immediate_riskoff_return=safe,
                        additional_loss_simple=safe-lev,additional_loss_relative_wealth=1-(1+lev)/(1+safe)))
    st=pd.DataFrame(stress)
    immediate=st[st.lag=='LAG1'].set_index(['series','event'])
    for i,row in st.iterrows():
        ref=immediate.loc[(row.series,row.event)]
        st.loc[i,'episode_end_wealth_difference_vs_lag1']=row.episode_end_value-ref.episode_end_value
        st.loc[i,'additional_episode_drawdown_vs_lag1']=ref.episode_max_drawdown-row.episode_max_drawdown
    st.to_csv(reports/'sma_stress_event_detail.csv',index=False,float_format='%.12g')
    pd.DataFrame(delay).to_csv(reports/'sma_stress_delay_transitions.csv',index=False,float_format='%.12g')
    pd.DataFrame({f'{name}__LAG{lag}__COST{cost}':r for (name,lag,cost),r in primary.items()}).to_csv(
        processed/'sma_falsification_daily_returns.csv',index_label='date',float_format='%.12g')
    pd.DataFrame({f'{name}__LAG{lag}':p for (name,lag,cost),p in primary_states.items() if cost==0}).to_csv(
        processed/'sma_falsification_positions.csv',index_label='date',float_format='%.12g')
    manifest={'as_of':config['as_of'],'entry_close':str(calendar[calendar.get_loc(ix[0])-1].date()),
        'input_sha256':{str(p.relative_to(root)):sha(p) for p in [root/'config.json',root/'data/processed/daily_returns.csv',
            root/'data/raw/fred_DTB3.csv',root/'data/snapshots/sma_price_inputs.zip']},
        'grid':{'sma_days':[150,200,250],'spread_bps':[0,50,100],'lag':[1,2],'switch_cost_bps':list(COSTS)},
        'runtime':{'python':platform.python_version(),'numpy':np.__version__,'pandas':pd.__version__}}
    (reports/'sma_falsification_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    from .falsification_report import write_report
    write_report(root,primary,daily.loc[ix,'SP500_1X'],state_vol,calendar)
    print(f'Falsification complete: {len(grid)} grid rows; {ix[0].date()}–{ix[-1].date()}',flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path.cwd())
    p.add_argument('--offline',action='store_true',help='Verify/restore original and price snapshots; no network')
    args=p.parse_args()
    run(args.root,offline=args.offline)


if __name__=='__main__': main()
