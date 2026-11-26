"""Stress actual option entry quotes without treating modeled prices as quotes.

Input CSV columns: name,spot,strike,ask,years,rate,dividend,budget.
Prices per underlying unit (not contract multiplier); rates/budget decimals.
All quotes must be from the same market close. European call approximation.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.optimize import brentq
from .options import black_scholes_call, call_delta

def evaluate(row, bill_yield, long_yield, duration=16., long_weight=0.,
             horizon=1., exit_spread=.01):
    spot,strike,ask,years,rate,q,budget = (
        float(row[k]) for k in ('spot','strike','ask','years','rate','dividend','budget'))
    if not (spot>0 and strike>0 and ask>0 and years>horizon>0 and
            0<budget<1 and 0<=long_weight<=1 and duration>=0 and 0<=exit_spread<1):
        raise ValueError('Invalid quote, horizon or reserve inputs')
    if not np.isfinite([spot,strike,ask,years,rate,q,budget,bill_yield,long_yield,duration]).all():
        raise ValueError('Nonfinite quote or reserve inputs')
    def price(s,r,v,t):
        return float(black_scholes_call(s,strike,r,q,v,t))
    iv=brentq(lambda v:price(spot,rate,v,years)-ask, .0001, 3.)
    delta=float(call_delta(spot,strike,rate,q,iv,years))
    contracts=budget/ask  # normalized portfolio NAV=1; fractional contracts
    rows=[]
    for move in (-.50,-.35,-.20,-.10,0.,.10,.20,.35,.50):
        for dy in (-.02,-.01,0.,.01,.02):
            for dv in (-.05,0.,.05):
                # First-order duration approximation in log-return units.
                # This is a sensitivity, not exact Treasury bond repricing.
                safe=(1-long_weight)*(1+bill_yield)**horizon + long_weight*np.exp(
                    np.log1p(long_yield)*horizon-duration*dy)
                mark=price(spot*(1+move),rate+dy,max(.01,iv+dv),years-horizon)
                wealth=contracts*mark*(1-exit_spread)+(1-budget)*safe
                rows.append(dict(name=row['name'],moneyness=strike/spot,budget=budget,
                    entry_iv=iv,entry_delta_exposure=contracts*delta*spot,
                    long_weight=long_weight,equity_price_return=move,
                    yield_shock=dy,iv_shock=dv,portfolio_return=wealth-1,
                    option_return=mark*(1-exit_spread)/ask-1,
                    reserve_return=safe-1,
                    excess_over_bills=wealth-(1+bill_yield)**horizon))
    return pd.DataFrame(rows)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('quotes',type=Path)
    p.add_argument('--bill-yield',type=float,required=True)
    p.add_argument('--long-yield',type=float,required=True)
    p.add_argument('--duration',type=float,default=16.)
    p.add_argument('--horizon',type=float,default=1.)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    quotes=pd.read_csv(a.quotes)
    out=pd.concat([evaluate(row,a.bill_yield,a.long_yield,a.duration,w,a.horizon)
                   for _,row in quotes.iterrows() for w in (0.,.5,1.)],ignore_index=True)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    out.to_csv(a.output,index=False)
if __name__=='__main__':
    main()
