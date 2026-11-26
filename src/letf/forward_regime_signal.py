"""Forward-looking equity premium screening using supplied point-in-time data.

No vendor data is fabricated or downloaded. Inputs are observable estimates
available by the decision close; scenario assumptions remain explicit.
This is a research screen, not a calibrated return forecast or a LEAPS optimizer.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

REQUIRED = ('decision_date','published_date','index','forward_pe','dividend_yield',
            'nominal_eps_growth','cash_yield','realized_vol','inflation')

def evaluate(row, terminal_pe, horizon=10., spread=.005):
    decision = pd.Timestamp(row['decision_date'])
    published = pd.Timestamp(row['published_date'])
    if pd.isna(decision) or pd.isna(published) or published > decision:
        raise ValueError('Estimate not available at decision date')
    pe, div, eps, cash, vol, infl = (float(row[k]) for k in REQUIRED[3:])
    if not np.isfinite([pe,div,eps,cash,vol,infl,terminal_pe,horizon,spread]).all():
        raise ValueError('Nonfinite inputs')
    if (pe<=0 or terminal_pe<=0 or horizon<=0 or vol<=0 or div<0 or
            eps<=-1 or cash<=-1 or infl<=-1 or spread<0):
        raise ValueError('Invalid input domain')
    # Constant dividend yield, EPS already per share (do not add buybacks).
    # The change in valuation is amortized over an explicit horizon.
    valuation = np.log(terminal_pe/pe)/horizon
    equity = np.log1p(eps)+np.log1p(div)+valuation
    premium = equity-np.log1p(cash)
    variance=vol**2
    hurdle2=variance+spread
    hurdle3=1.5*variance+spread
    state='green' if premium>hurdle3 else 'amber' if premium>hurdle2 else 'red'
    return dict(decision_date=decision.date().isoformat(),index=row['index'],
        published_date=published.date().isoformat(),forward_pe=pe,
        terminal_pe=terminal_pe,horizon_years=horizon,
        nominal_eps_growth=eps,dividend_yield=div,cash_yield=cash,
        realized_vol=vol,inflation=infl,valuation_log_drag=valuation,
        assumed_real_equity_cagr=np.expm1(equity-np.log1p(infl)),
        assumed_log_equity_premium=premium, hurdle_2x=hurdle2,hurdle_3x=hurdle3,
        margin_2x=premium-hurdle2,margin_3x=premium-hurdle3,state=state,
        required_eps_growth_2x=np.expm1(hurdle2+np.log1p(cash)-np.log1p(div)-valuation),
        required_eps_growth_3x=np.expm1(hurdle3+np.log1p(cash)-np.log1p(div)-valuation))

def grid(frame, horizon=10.):
    missing=set(REQUIRED)-set(frame.columns)
    if missing:
        raise ValueError(f'Missing columns: {sorted(missing)}')
    out=[]
    for _,row in frame.iterrows():
        # Separate uncertainty in future earnings from current valuation.
        # No scenario is assigned a probability.
        for growth_shift in (-.03,0.,.03):
            case=row.copy()
            case['nominal_eps_growth']=float(row['nominal_eps_growth'])+growth_shift
            for pe_ratio in (.70,.85,1.,1.15):
                result=evaluate(case,float(row['forward_pe'])*pe_ratio,horizon)
                result.update(eps_growth_shift=growth_shift,terminal_pe_ratio=pe_ratio)
                out.append(result)
    return pd.DataFrame(out)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('inputs',type=Path)
    p.add_argument('--horizon',type=float,default=10.)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    out=grid(pd.read_csv(a.inputs),a.horizon)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    out.to_csv(a.output,index=False)

if __name__=='__main__':
    main()
