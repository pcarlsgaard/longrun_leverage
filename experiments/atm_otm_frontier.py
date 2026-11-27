"""Isolated ATM/OTM frontier experiment; does not overwrite published reports.

Uses the existing daily joint block bootstrap, roll calendar, option engine,
path metrics and epsilon frontiers. Skew arms are static strike-at-purchase
loadings, not an observed or dynamically repriced historical surface.
"""
from __future__ import annotations
import argparse
from dataclasses import replace
from pathlib import Path
import json
import numpy as np
import pandas as pd
from scipy.optimize import brentq
from letf import leaps_frontier as f
from letf.leaps_robustness import block_draw
from letf.options import roll_schedule, black_scholes_call, call_delta

STRIKES = tuple(i / 100 for i in range(80, 126, 5))
BUDGETS = tuple(i / 100 for i in range(10, 51, 5))
ARMS = {
    # maturity years, roll interval years, per-side spread bps, ATM IV loading,
    # skew slope (absolute IV per unit strike/spot), annual log growth shift
    "baseline": (2, 1, 100, .03, 0, 0),
    "iv0": (2, 1, 100, 0, 0, 0),
    "iv6": (2, 1, 100, .06, 0, 0),
    "skew_half": (2, 1, 100, .03, -.075, 0),
    "skew_full": (2, 1, 100, .03, -.15, 0),
    "cost0": (2, 1, 0, .03, 0, 0),
    "cost2": (2, 1, 200, .03, 0, 0),
    "cost4": (2, 1, 400, .03, 0, 0),
    "skew_cost4": (2, 1, 400, .03, -.15, 0),
    "duration30": (2.5, 1, 100, .03, 0, 0),
    "duration36": (3, 1, 100, .03, 0, 0),
    "roll6": (2, .5, 100, .03, 0, 0),
    "roll18": (2, 1.5, 100, .03, 0, 0),
    "growth_low": (2, 1, 100, .03, 0, -.04),
    "growth_high": (2, 1, 100, .03, 0, .03),
}
ANCHORS = ("NDX_80_25", "NDX_85_30", "NDX_90_40", f.NASDAQ)

def run_path(sample, horizon, spec):
    arm, slope, shift = spec
    if shift:
        sample = sample.copy()
        # Change Nasdaq price AND total return by the same multiplicative
        # increment; preserve daily shock ranks, dividend and joint bond rows.
        for col in (f.NDX_PRICE, f.NDX_TOTAL):
            sample[:, col] = (1 + sample[:, col]) * np.exp(shift / 252) - 1
    if slope == 0:
        return f.run_path(sample, horizon, (arm,))
    # Use exactly the original path runner for each strike group, modifying
    # its volatility loading only. No new accounting or option return series.
    original = f.STRUCTURES
    result = {}
    try:
        for strike in sorted({x[0] for x in original.values()}):
            f.STRUCTURES = {k: v for k, v in original.items() if v[0] == strike}
            priced = replace(arm, iv_premium=arm.iv_premium + slope * (strike - 1))
            result.update(f.run_path(sample, horizon, (priced,)))
    finally:
        f.STRUCTURES = original
    return result

def register(sizing):
    if sizing == "budget":
        structures = f._cells("NDX", STRIKES, BUDGETS)
    else:
        structures = {f"NDX_{round(m*100)}_D{round(d*100)}": (m, .30)
                      for m in STRIKES for d in (.8, 1.2, 1.6)}
    f.STRUCTURES = structures
    f.UNDERLYING = {name: f.NASDAQ for name in structures}
    f.FAMILY = {name: "NDX LEAPS" for name in structures}

def rule_for(name, sizing, maturity, interval, cost):
    m, b = f.STRUCTURES[name]
    rule = replace(f.leaps_rule(m, b, 1.), maturity_years=maturity,
                   roll_at_years=maturity-interval, spread_bps=cost)
    if sizing == "delta":
        rule = replace(rule, premium_budget=None,
                       target_exposure=int(name.split("_D")[1])/100)
    return rule

def annual_hurdles(args, maturity, interval, cost, premium, slope):
    """Constant-rate/IV, illustrative break-even until the intended roll.
    Separate from historical returns; not an expiration break-even."""
    rows = []
    for name, (m, b) in f.STRUCTURES.items():
        rule = rule_for(name, args.sizing, maturity, interval, cost)
        sigma = .24 + premium + slope*(m-1)
        r, q, s = .046, .005, 1.
        c = float(black_scholes_call(s, m, r, q, sigma, maturity))
        d = float(call_delta(s, m, r, q, sigma, maturity))
        entry = c*(1+cost/10000)
        budget = b if args.sizing == "budget" else min(1., rule.target_exposure*entry/d)
        def value(x):
            exit_value = float(black_scholes_call(x, m, r, q, sigma, maturity-interval))
            return budget*exit_value*(1-cost/10000)/entry+(1-budget)*np.exp(r*interval)
        hurdle = brentq(lambda x: value(x)-1., .01, 10.)
        rows.append(dict(strategy=name, strike=m, option_budget=budget,
                         initial_delta=budget*d/entry, flat_return=value(1)-1,
                         portfolio_break_even_annualized=hurdle**(1/interval)-1))
    return pd.DataFrame(rows)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path.cwd())
    p.add_argument("--out", type=Path, default=Path("experiment-results"))
    p.add_argument("--arm", choices=ARMS, default="baseline")
    p.add_argument("--sizing", choices=("budget","delta"), default="budget")
    p.add_argument("--years", type=int, choices=(10,20,30), default=30)
    p.add_argument("--paths", type=int, default=3000)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--block", type=int, default=f.PRIMARY_BLOCK)
    args=p.parse_args()
    maturity, interval, cost, premium, slope, shift = ARMS[args.arm]
    inputs=f.load(args.root)
    market,_=f.underlying_inputs(inputs,args.root)
    pool=f.frontier_pool(inputs,market)
    arm=f.Arm("baseline",iv_premium=premium)
    # Validate path-by-path identity before expanding the original lattice.
    if args.arm=="baseline" and args.sizing=="budget":
        old_h=f.build_horizon(inputs,args.years,(f.ROLL_LABEL,),structures=f.STRUCTURES)
        seed=np.random.SeedSequence(f.SEED).spawn(1)[0]
        sample=pool[block_draw(np.random.default_rng(seed),len(pool),
                              len(old_h.calendar)-1,args.block)]
        old=f.run_path(sample,old_h,(arm,))
    else:
        old=None
    register(args.sizing)
    horizon=f.build_horizon(inputs,args.years,(f.ROLL_LABEL,),structures=f.STRUCTURES)
    horizon=replace(horizon,schedules={(name,arm.roll):roll_schedule(
        horizon.closes,rule_for(name,args.sizing,maturity,interval,cost))
        for name in f.STRUCTURES})
    if old is not None:
        new=run_path(sample,horizon,(arm,slope,shift))
        for name in ANCHORS:
            k=f.key(arm.name,name)
            np.testing.assert_allclose(new[k],old[k],rtol=1e-12,atol=1e-12,equal_nan=True)
        print("Original anchor path equivalence passed.",flush=True)
    case=f.Case(args.arm,args.block,args.paths,(arm,),args.arm)
    store=f.monte_carlo(pool,horizon,(arm,slope,shift),args.paths,
                        args.block,f.SEED,args.workers,runner=run_path)
    frame=f.summarize(store,horizon,case,paired=False)
    frame=f.frontier_flags(frame)
    frame["sizing"]=args.sizing
    frame["maturity_years"]=maturity
    frame["nominal_roll_years"]=interval
    frame["spread_bps_per_side"]=cost
    frame["skew_slope"]=slope
    frame["annual_log_growth_shift"]=shift
    frame["entry_iv_loading"]=premium+slope*(frame.moneyness-1)
    if args.sizing=="delta":
        frame["target_delta"]=frame.strategy.str.extract(r"_D(\d+)")[0].astype(float)/100
        frame["premium_budget"]=np.nan
    args.out.mkdir(parents=True,exist_ok=True)
    prefix=f"{args.arm}_{args.sizing}_{args.years}y_{args.paths}p"
    if (args.arm=="baseline" and args.sizing=="budget" and
        args.paths==f.MC_PATHS and args.block==f.PRIMARY_BLOCK):
        prior=pd.read_csv(args.root/"reports/leaps_frontier_monte_carlo.csv")
        prior=prior[(prior.horizon_years==args.years)&prior.strategy.isin(ANCHORS)].set_index("strategy")
        observed=frame.set_index("strategy").loc[list(ANCHORS)]
        cols=["cagr_p50","cagr_p5","max_drawdown_median","prob_drawdown_worse_than_60"]
        np.testing.assert_allclose(observed[cols],prior.loc[list(ANCHORS),cols],
                                   rtol=1e-7,atol=1e-9)
        print("Published 15,000-path anchor reproduction passed.",flush=True)
    frame.to_csv(args.out/f"{prefix}.csv",index=False)
    annual_hurdles(args,maturity,interval,cost,premium,slope).to_csv(
        args.out/f"{prefix}_hurdles.csv",index=False)
    # Realized historical path, same frozen inputs and core, for the flat,
    # unshifted scenario. Sensitivity arms focus on the paired bootstrap.
    if args.arm=="baseline":
        rows=[]
        for name in f.STRUCTURES:
            spot,q,r,vol=market[f.NASDAQ]
            sleeve=inputs.daily.loc[inputs.ix,f.TREASURY]
            rule=rule_for(name,args.sizing,maturity,interval,cost)
            path=f.simulate_leaps_arrays(*f.leaps_arrays(spot,sleeve,q,r,vol),
                                        roll_schedule(spot.index,rule))
            returns=pd.Series(path.navs,index=spot.index).pct_change().dropna()
            row=f.describe(name,returns,inputs,path)
            closed=path.rolls[:-1]
            row["fraction_losing_option_rolls"]=float(np.mean([
                x["exit_premium_ratio"]*(1-cost/10000)/(1+cost/10000)<1
                for x in closed])) if closed else np.nan
            if args.sizing=="delta":
                row["target_delta"]=rule.target_exposure
                row["premium_budget"]=np.nan
            rows.append(row)
        pd.DataFrame(rows).to_csv(args.out/f"{prefix}_historical.csv",index=False)
    (args.out/f"{prefix}_manifest.json").write_text(json.dumps({
        "seed":f.SEED,"block":args.block,"paths":args.paths,"years":args.years,
        "arm":args.arm,"parameters":ARMS[args.arm],"sizing":args.sizing,
        "volatility_lookback":"Original lagged 504-session proxy, including duration arms",
        "skew":"Static entry-strike loading held for contract life; not historical quotes",
        "inputs_window":[str(inputs.ix[0]),str(inputs.ix[-1])],
        "pricing":"Existing European Black-Scholes approximation, not QQQM live execution",
        "annual_hurdles":"Illustrative 24% RV, 4.6% rate, 0.5% dividend; nominal tenor and roll"
    },indent=2)+"\n")
    print(frame.loc[frame.on_frontier,["strategy","cagr_p50","cagr_p5",
          "max_drawdown_median","prob_drawdown_worse_than_60"]].to_string(index=False))

if __name__=="__main__":
    main()
