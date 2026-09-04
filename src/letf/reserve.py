"""Capital-reserve state machine. Frozen returns in; self-financing ledger out.

Return-end row t executes at close t-1. Signal/portfolio observations come from
close t-lag. Scheduled fixed rebalances use the same extra-session delay.
Costs are bps per one-way allocation transition (sell+buy), as in falsification.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ReserveRule:
    kind: str = 'none'
    deployment: str = 'staged'
    initial: float = 0.
    cap: float = .15
    harvest: float = .10
    fixed: float = .10
    frequency: str = 'quarterly'

    def __post_init__(self):
        if self.kind not in ('none', 'fixed', 'hwm', 'band'):
            raise ValueError('Unknown reserve rule')
        if self.deployment not in ('staged', 'drawdown', 'hold'):
            raise ValueError('Unknown deployment rule')
        if not all(0 <= v <= 1 for v in (self.initial, self.cap, self.harvest, self.fixed)):
            raise ValueError('Invalid reserve parameters')
        if self.frequency not in ('monthly', 'quarterly', 'annual'):
            raise ValueError('Invalid frequency')


def transition_turnover(risky, reserve, transfer, switch):
    """Half-L1 across the actual assets; net overlapping switch/transfer orders.

    Positive transfer buys bills. On a state switch, selling the old risky asset
    and buying the new asset and/or bills is ONE transition on max(R,R-transfer).
    """
    if risky < 0 or reserve < 0 or not -reserve-1e-12 <= transfer <= risky+1e-12:
        raise ValueError('Transfer exceeds capital')
    return max(risky, risky-transfer) if switch else abs(transfer)


def simulate_reserve(risky_returns, cash_returns, position, drawdown_signal,
                     calendar, rule=ReserveRule(), leverage=3, lag=1, cost_bps=0,
                     off_leverage=1, ledger=True):
    """No external flows. HWM starts at initial wealth 1, never resets on loss.

    HWM signal recognizes each new high once even if cap prevents harvesting.
    Cap limits purchases, not subsequent passive cash-weight drift in crashes.
    Band starts at 10%; following a deployment it can only rebuild on a new
    high of the unreserved risky strategy, observed AFTER that deployment.
    Stage counts: first executed favorable session, then 20 and 60 completed
    favorable sessions (return rows 1/21/61). First/second tranches are thirds
    of the episode-entry bill units, with accrued interest. Final stage empties
    remaining bills, including subsequent harvests. New episodes re-split bills.
    """
    ix = risky_returns.index
    inputs = pd.concat([risky_returns, cash_returns, position, drawdown_signal], axis=1)
    if (not all(s.index.equals(ix) for s in (cash_returns, position, drawdown_signal))
            or inputs.isna().any().any() or not np.isfinite(inputs.to_numpy()).all()
            or not position.isin([0, 1]).all() or lag not in (1, 2)
            or not 0 <= cost_bps < 10000 or (inputs.iloc[:, :2] <= -1).any().any()):
        raise ValueError('Invalid or terminated inputs; no missing-day filling')
    start = calendar.get_loc(ix[0])
    if start < 1 or not ix.equals(calendar[start:start+len(ix)]):
        raise ValueError('Missing trading session or entry close')
    closes = calendar[start-1:start+len(ix)]
    rr, cr, ps, dd = (s.to_numpy() for s in (risky_returns, cash_returns, position, drawdown_signal))
    initial = rule.fixed if rule.kind == 'fixed' else rule.initial
    r, c = 1-initial, initial
    navs, reserves, sleeve = [1.], [initial], [1.]
    hwm, risky_hwm = 1., 1.
    streak, completed, last_deploy = 0, 0, -100
    locked, thresholds = False, set()
    episode_cash_units = 0.
    recovery_cash_units = 0.
    cash_nav = np.r_[1., np.cumprod(1+cr)]
    period = ix.to_period({'monthly':'M','quarterly':'Q','annual':'Y'}[rule.frequency])
    scheduled = np.r_[False, period[1:] != period[:-1]]
    if lag == 2:
        scheduled = np.r_[False, scheduled[:-1]]
    records, trades = [], []
    for i in range(len(ix)):
        before = r+c
        switch = i > 0 and ps[i] != ps[i-1]
        favorable = ps[i] == 1
        new_episode = i > 0 and favorable and ps[i-1] == 0
        streak = streak+1 if favorable else 0
        if new_episode:
            completed = 0
        # Observe only lag-eligible close; the arrays include entry wealth at 0.
        obs = i+1-lag
        new_gain, new_risky_high = 0., False
        if obs >= 0:
            new_gain = max(0., navs[obs]-hwm)
            hwm = max(hwm, navs[obs])  # recognition advances even at cap
            new_risky_high = sleeve[obs] > risky_hwm+1e-14
            risky_hwm = max(risky_hwm, sleeve[obs])
        amount, stage, reason = 0., '', ''
        dynamic = rule.kind in ('hwm', 'band')
        if dynamic and rule.deployment == 'staged':
            if new_episode:
                recovery_cash_units = c/cash_nav[i]
                amount, completed, stage = -c/3, 1, 'recovery_1_of_3'
            elif favorable and completed == 1 and streak >= 21:
                amount, completed, stage = -min(c, recovery_cash_units*cash_nav[i]/3), 2, 'recovery_2_of_3'
            elif favorable and completed == 2 and streak >= 61:
                amount, completed, stage = -c, 3, 'recovery_3_of_3'
        if dynamic and rule.deployment == 'drawdown':
            if dd[i] >= -1e-12:
                thresholds.clear()
                episode_cash_units = 0.
            hit = [j for j, level in enumerate((-.20, -.30, -.40, -.50))
                   if dd[i] <= level+1e-12 and j not in thresholds]
            if hit:
                if not thresholds:
                    episode_cash_units = c/cash_nav[i]
                fraction = sum((.20,.25,.25,.30)[j] for j in hit)
                amount = -min(c, episode_cash_units*cash_nav[i]*fraction)
                thresholds.update(hit)
                stage = 'drawdown_' + '_'.join(str((20,30,40,50)[j]) for j in hit)
        if amount < -1e-15:
            locked, last_deploy, reason = True, i, 'deployment'
        else:
            amount = 0.
            if rule.kind == 'fixed' and scheduled[i]:
                amount, reason = before*rule.fixed-c, 'fixed_rebalance'
            elif rule.kind == 'hwm':
                amount = min(rule.harvest*new_gain, max(0., rule.cap*before-c), r)
                reason = 'gain_harvest'
            elif rule.kind == 'band' and favorable:
                # No reverse trade at a deployment close; observed high must
                # occur after the last deployment, not be an older queued high.
                allowed = not locked or (new_risky_high and obs > last_deploy)
                if allowed and c/before < .05:
                    amount, reason = min(r, max(0., .10*before-c)), 'band_rebuild'
                    locked = False
        amount = min(r, max(-c, amount))
        traded = transition_turnover(r, c, amount, switch)
        fee = traded*cost_bps/10000
        factor = 1-fee/before
        # Apply one proportional cost haircut to post-transfer holdings.
        r0, c0 = (r-amount)*factor, (c+amount)*factor
        weight = c0/(r0+c0)
        state_leverage = leverage if favorable else off_leverage
        actual_deploy = max(0., -amount)*factor
        # Existing tagged risky capital is sold proportionally on accumulation.
        retention = (1-max(amount,0)/r if r else 1)*factor*(1+rr[i])
        r, c = r0*(1+rr[i]), c0*(1+cr[i])
        value = r+c
        navs.append(value); reserves.append(c)
        sleeve.append(sleeve[-1]*(1+rr[i]))
        if not ledger:
            continue
        if abs(amount) > 1e-15:
            trades.append(dict(return_end=ix[i], execution_close=closes[i],
                signal_close=closes[max(0,i+1-lag)], reason=reason, stage=stage,
                transfer_to_reserve=amount, net_deployed=actual_deploy,
                asset=('LEVERAGED' if favorable else ('SP500_1X' if off_leverage else 'TBILL_3M_1X')),
                favorable=bool(favorable), streak=streak, row=i))
        records.append(dict(wealth=value, risky_wealth=r, reserve_wealth=c,
            return_net=value/before-1, start_reserve_weight=weight,
            reserve_weight=c/value, effective_equity_exposure=(1-weight)*state_leverage,
            sleeve_leverage=state_leverage, transfer_to_reserve=amount,
            accumulation=max(amount,0), deployment=max(-amount,0) if reason=='deployment' else 0.,
            state_switch=int(switch), reserve_turnover=abs(amount)/before,
            risky_sleeve_turnover=(traded if switch else 0.)/before,
            total_turnover=traded/before, transaction_cost=fee,
            cost_fraction=fee/before, retention=retention,
            reserve_cash_income=c0*cr[i], hwm_observed=hwm, favorable=favorable))
    if not ledger:
        return pd.Series(navs[1:], index=ix, name="wealth")
    return pd.DataFrame(records,index=ix), pd.DataFrame(trades)


def full_cycle_accounting(no_pre, reserve_pre, no_after, reserve_after):
    """Dollar identity at a common later date, same original initial capital.

    Post benefit compares actual wealth with continuation at no-reserve growth
    starting from reserve wealth at the peak. Carry the peak shortfall forward
    at that SAME growth rate: benefit - carried cost == full-cycle advantage.
    This is path attribution, not causal profit attributable only to deployments.
    """
    growth = no_after/no_pre
    cost = no_pre-reserve_pre
    benefit = reserve_after-reserve_pre*growth
    carried = cost*growth
    return dict(pre_drawdown_opportunity_cost=cost,
                carried_opportunity_cost=carried,
                incremental_post_drawdown_wealth=benefit,
                full_cycle_advantage=reserve_after-no_after,
                reserve_payback_ratio=benefit/carried if carried > 1e-12 else np.nan,
                recovery_multiplier=reserve_after/no_after,
                attribution_residual=benefit-carried-(reserve_after-no_after))


def tagged_deployment_value(ledger, trade, end_row):
    """Remaining marked risky wealth from a tranche after proportional harvesting.

    Retention includes later switches/costs and sales back to reserve. It excludes
    recirculated proceeds, hence is a conservative remaining-lot measure, not P&L.
    """
    i = int(trade['row'])
    if end_row < i:
        return 0.
    first_growth = (ledger.iloc[i].risky_wealth /
                    (ledger.iloc[i].wealth/(1+ledger.iloc[i].return_net)
                     * (1-ledger.iloc[i].cost_fraction)
                     * (1-ledger.iloc[i].start_reserve_weight)))
    return trade['net_deployed']*first_growth*ledger.retention.iloc[i+1:end_row+1].prod()
