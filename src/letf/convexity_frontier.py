"""Rolling Nasdaq call spreads, paired price-regime and economic stress tests.

Model prices, not observed options. Same-expiry short calls are covered by long
calls; 0 <= overwrite <= 1. Prices use the original lagged volatility proxy
plus explicit loadings. Upper-call time-value haircut is a pairwise bounded
credit stress, not an arbitrage-free full implied volatility surface.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from .options import black_scholes_call, call_delta, YEAR
from .leaps_robustness import (load, build_horizon, build_path, block_draw,
                              WARMUP_SESSIONS, CASH_R, TREASURY_R)
from .leaps_frontier import frontier_pool, NDX_PRICE, NDX_TOTAL, NDX_YIELD
from .nasdaq_leaps import underlying_inputs
from .secular_regime_stress import (transform, FAVORABLE, NORMAL, AI_EXPENSIVE,
                                    FISCAL, metrics, nav)

LONGS=(.80,.85,.90)
SHORTS=(1.20,1.35,1.50)
OVERWRITES=(.5,1.)
# Screen only; no fine tuning. Same draws across every arm.
CASES=(('historical',None),('favorable',FAVORABLE),
       ('normalization',NORMAL),('ai_expensive',AI_EXPENSIVE),('fiscal',FISCAL))
PRICING=(('base',.03,.03,0.),('expensive',.09,.09,0.),
         ('expensive_skew',.09,.09,.20),('entry_only',.09,.03,.20))
SIZES=(('budget30',.30,None),('budget40',.40,None),
       ('delta100',.50,1.),('delta125',.50,1.25))

def flat_marks(spot,q,r,iv,days,schedule):
    starts=schedule.starts
    ends=np.r_[starts[1:],len(spot)-1]
    lengths=ends-starts+1
    offsets=np.r_[0,np.cumsum(lengths)]
    segment=np.repeat(np.arange(len(starts)),lengths)
    flat=np.arange(offsets[-1])-offsets[segment]+starts[segment]
    t=np.maximum((schedule.expiry_days[segment]-days[flat])/YEAR,0.)
    return starts,ends,offsets,segment,flat,t

def call_marks(spot,q,r,iv,geometry,strike_ratio):
    starts,_,_,segment,flat,t=geometry
    k=strike_ratio*spot[starts][segment]
    s=spot[flat]
    p=black_scholes_call(s,k,r[flat],q[flat],iv[flat],t)
    d=call_delta(s,k,r[flat],q[flat],iv[flat],t)
    return p,d,k

def pair_marks(long,short,spot,q,r,geometry,haircut):
    """Stress upper credit, respecting pairwise discounted width and forward floor.

    Delta is the derivative of the selected pricing branch at fixed IV/rate/q
    and fixed strikes (away from branch boundaries).
    """
    lp,ld,lk=long
    sp,sd,sk=short
    _,_,_,_,flat,t=geometry
    disc_q=np.exp(-q[flat]*t)
    disc_r=np.exp(-r[flat]*t)
    floor=np.maximum(spot[flat]*disc_q-sk*disc_r,0.)
    fd=np.where(floor>0,disc_q,0.)
    raw=(1-haircut)*sp+haircut*floor
    raw_d=(1-haircut)*sd+haircut*fd
    bound=lp-(sk-lk)*disc_r
    # If binding, reduce the haircut so the vertical never exceeds its width.
    use=bound>raw
    return np.maximum(raw,bound),np.where(use,ld,raw_d),use

def roll_portfolio(spot,safe,geometry,long,short,overwrite,budget,target=None,spread=.01):
    """Leg-level fills: pay ask on long, receive bid on short, reverse on exit.
    Maximum premium budget is a hard cap even for matched-delta sizing.
    NAV is marked at mid between rolls, matching original final-mark convention.
    """
    starts,ends,offsets,_,_,_=geometry
    lp,ld,_=long
    sp,sd=short
    wealth=1.
    w=np.ones(len(spot))
    exposures=[]
    caps=[]
    for i,(a,b) in enumerate(zip(starts,ends)):
        sl=slice(offsets[i],offsets[i+1])
        l,s=lp[sl],sp[sl]*overwrite
        delta=ld[sl]-overwrite*sd[sl]
        entry=l[0]*(1+spread)-s[0]*(1-spread)
        if entry<=0 or not np.isfinite(entry):
            raise ValueError('Nonpositive spread entry')
        n=budget*wealth/entry
        if target is not None:
            wanted=target*wealth/(delta[0]*spot[a]) if delta[0]>0 else np.inf
            caps.append(wanted>n)
            n=min(n,wanted)
        reserve=wealth-n*entry
        carry=np.cumprod(np.r_[1.,1+safe[a:b]])
        marked=n*(l-s)+reserve*carry
        if np.any(marked<=0):
            raise ValueError('Nonpositive marked wealth')
        w[a+1:b+1]=marked[1:]
        exposures.append(n*delta[0]*spot[a]/wealth)
        # Exit costs are realized at roll, not silently netted on spread value.
        wealth=n*(l[-1]*(1-spread)-s[-1]*(1+spread))+reserve*carry[-1]
        if wealth<=0 and i<len(starts)-1:
            raise ValueError('Insufficient funds to close')
    return w,float(np.mean(exposures)),float(np.mean(caps)) if caps else 0.

def screen(root,output,paths=64,seed=20261126,years=20):
    inputs=load(root)
    market,_=underlying_inputs(inputs,root)
    pool=frontier_pool(inputs,market)
    h=build_horizon(inputs,years,('12m',),structures={'base':(.90,.40)})
    sched=h.schedules[('base','12m')]
    n=len(h.calendar)-1
    e=WARMUP_SESSIONS
    draws=[block_draw(np.random.default_rng(seed+i),len(pool),n,63) for i in range(paths)]
    rows=[]
    for case,regime in CASES:
        for i,draw in enumerate(draws):
            sample=pool[draw]
            if regime is not None:
                sample=transform(sample,pool,regime)
            spot,rv,r,q,_=build_path(sample,h.calendar,signal=False,price_col=NDX_PRICE,
                                    yield_col=NDX_YIELD,iv_premium=0.)
            spot,rv,r,q=(x[e:] for x in (spot,rv,r,q))
            cash=sample[e:,CASH_R]
            bond=sample[e:,TREASURY_R]
            sleeves=(('bills',cash),('mixed',.5*(cash+bond)))
            geometry=flat_marks(spot,q,r,rv,h.days,sched)
            _,_,_,_,flat,t=geometry
            for arm,initial,ongoing,skew in PRICING:
                # Entry-only premium decays over the first year. The strategy
                # sees current marks, never the future pricing path.
                decay=np.maximum(1-h.days/365.25,0.)
                iv=np.clip(rv+ongoing+(initial-ongoing)*decay,.05,1.5)
                marks={k:call_marks(spot,q,r,iv,geometry,k) for k in LONGS+SHORTS}
                def save(name,w,delta,cap,floor=0.):
                    cagr,dd,_=metrics(w,h.elapsed_days/365.25)
                    rows.append(dict(case=case,pricing=arm,path=seed+i,strategy=name,
                        real_cagr=cagr,drawdown=dd,entry_delta=delta,cap_fraction=cap,
                        credit_bound_fraction=floor))
                save('NDX_1X',nav(sample[e:,NDX_TOTAL]),1.,0.)
                save('BILLS',nav(cash),0.,0.)
                for lo in LONGS:
                    long=marks[lo]
                    variants=[(0.,0.,(np.zeros_like(long[0]),np.zeros_like(long[1])),0.)]
                    for hi in SHORTS:
                        sp,sd,bound=pair_marks(long,marks[hi],spot,q,r,geometry,skew)
                        for f in OVERWRITES:
                            variants.append((hi,f,(sp,sd),float(bound.mean())))
                    for hi,f,short,bound in variants:
                        for size,budget,target in SIZES:
                            for sleeve,safe in sleeves:
                                name=f'L{lo:.2f}_U{hi:.2f}_F{f:.2f}_{size}_{sleeve}'
                                w,d,c=roll_portfolio(spot,safe,geometry,long,short,f,budget,target)
                                save(name,w,d,c,bound)
        print(case,paths,'paired paths',flush=True)
    output.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(rows).to_csv(output/'paths.csv',index=False)
    summarize(pd.DataFrame(rows),output)

def summarize(df,output):
    base=df[df.strategy=='NDX_1X'].set_index(['case','pricing','path']).real_cagr
    df=df.join(base.rename('ndx'),on=['case','pricing','path'])
    df['win']=df.real_cagr>df.ndx
    df['dd60']=df.drawdown<-.6
    s=df.groupby(['case','pricing','strategy']).agg(paths=('path','size'),
        median_cagr=('real_cagr','median'),p5_cagr=('real_cagr',lambda x:x.quantile(.05)),
        p_dd60=('dd60','mean'),p_beat_ndx=('win','mean'),
        mean_entry_delta=('entry_delta','mean'),mean_cap=('cap_fraction','mean'),
        credit_bound_fraction=('credit_bound_fraction','mean')).reset_index()
    s['pareto']=False
    for _,g in s.groupby(['case','pricing']):
        vals=g[['median_cagr','p5_cagr','p_dd60']].to_numpy()*[1,1,-1]
        dominates=((vals[:,None,:]>=vals[None,:,:]).all(2)&
                   (vals[:,None,:]>vals[None,:,:]).any(2)).any(0)
        s.loc[g.index,'pareto']=~dominates
    s.to_csv(output/'summary.csv',index=False)
    # Paired gain vs same strike, budget/target, and reserve, never cherry-pick rival.
    p=df[df.strategy.str.startswith('L')].copy()
    p['reference']=p.strategy.str.replace(r'_U[0-9.]+_F[0-9.]+_', '_U0.00_F0.00_',regex=True)
    ref=p.set_index(['case','pricing','path','strategy'])[['real_cagr','drawdown']]
    p=p.join(ref.rename(columns={'real_cagr':'ref_cagr','drawdown':'ref_dd'}),
             on=['case','pricing','path','reference'])
    p['gain']=p.real_cagr-p.ref_cagr
    p['win_vs_out']=p.gain>0
    p['extra_dd']=p.ref_dd-p.drawdown
    p.groupby(['case','pricing','strategy','reference']).agg(
        median_gain=('gain','median'),p5_gain=('gain',lambda x:x.quantile(.05)),
        p_win=('win_vs_out','mean'),median_extra_dd=('extra_dd','median')
        ).reset_index().to_csv(output/'paired.csv',index=False)
    return s

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path.cwd())
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--paths',type=int,default=64)
    p.add_argument('--seed',type=int,default=20261126)
    p.add_argument('--years',type=int,default=20,choices=[10,20,30])
    a=p.parse_args()
    if a.paths<1: p.error('paths must be positive')
    screen(a.root,a.output,a.paths,a.seed,a.years)
if __name__=='__main__':
    main()
