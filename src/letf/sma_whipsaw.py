"""Prespecified SMA whipsaw falsification; never rebuilds invested-return histories.

Close-t price signals affect return ending t+lag, never the signal-close return.
A pending-sell cancellation inspects only intermediate CLOSES available before
the return whose sleeve is selected. Both sides pay the existing transition costs.
"""
from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path

import numpy as np
import pandas as pd

from .analysis import CASH, REGIMES, load_inputs
from .cohorts import cohort_cagrs, nav_path
from .falsification import evaluate, load_price_signals
from .model import matched
from .provenance import FLOAT_FORMAT, sha, stable_floats
from .signals import level_position
from .strategy import select_returns, switching_costs, transitions

LENGTHS = (185, 200)  # 185 is user-requested diagnostic, not an optimized cutoff.
LAGS = (1, 2, 3)       # 3 = two complete extra sessions beyond LAG1.
COSTS = (0, 10, 25, 50)
SPREAD_BPS = 50
RULES = ("BASE", "CONFIRM_BEAR2", "BUFFER_EXIT_50BP", "CANCEL_PENDING_SELL")
FUNDS = {"UPRO": "SP500_1X", "SSO": "SP500_1X", "TQQQ": "NASDAQ100_1X"}
PRIMARY_LENGTH, PRIMARY_COST = 200, 25


def raw_states(price: pd.Series, calendar: pd.DatetimeIndex, length: int, rule: str) -> pd.Series:
    """Unlagged states as of each close; first valid close is initial state.

    CONFIRM_BEAR2: two consecutive closes on/below SMA to de-lever;
    one close above SMA to re-lever. BUFFER_EXIT_50BP: exit at <= 99.5%
    of SMA, re-enter only above SMA. Both have no intraday information.
    """
    if rule not in RULES:
        raise ValueError("Unknown whipsaw rule")
    if length < 2:
        raise ValueError("Invalid SMA length")
    canonical = level_position(price, calendar, length, lag=1)
    price = price.loc[calendar[0]:calendar[-1]].reindex(calendar)
    sma = price.rolling(length, min_periods=length).mean()
    base = (price > sma).astype(float).where(sma.notna()).reindex(calendar)
    pd.testing.assert_series_equal(base.shift(1), canonical, check_names=False)
    if rule in ("BASE", "CANCEL_PENDING_SELL"):
        return base
    values = base.to_numpy()
    output = np.full(len(base), np.nan)
    previous = None
    bearish_run = 0
    for i, signal in enumerate(values):
        if np.isnan(signal):
            continue
        if previous is None:
            previous = int(signal)  # no position assumed before warm-up
        if rule == "CONFIRM_BEAR2":
            bearish_run = bearish_run + 1 if signal == 0 else 0
            if bearish_run >= 2:
                previous = 0
            elif signal == 1:
                previous = 1
        else:
            if previous == 1 and price.iloc[i] <= sma.iloc[i] * 0.995:
                previous = 0
            elif previous == 0 and signal == 1:
                previous = 1
        output[i] = previous
    return pd.Series(output, index=calendar, name=None)


def executed_states(raw: pd.Series, lag: int, cancel_pending_sell: bool = False) -> pd.Series:
    """Map an observed close to a future return-end state without peeking.

    When lag>=2 and a new de-leveraging order is still pending, cancel it if
    ANY intermediate close recovered above the SMA. An already-executed sale
    is never canceled retroactively; buy orders are not canceled.
    """
    if lag not in LAGS or (cancel_pending_sell and lag == 1):
        raise ValueError("Cancellation needs at least one intermediate close")
    planned = raw.shift(lag)
    if not cancel_pending_sell:
        return planned
    output = planned.copy()
    for i in range(lag, len(output)):
        target = planned.iat[i]
        if pd.isna(target):
            continue
        previous = output.iat[i - 1]
        if (target == 0 and previous == 1
                and (raw.iloc[i - lag + 1:i] == 1).any()):
            output.iat[i] = 1.
    return output


def whipsaws(position: pd.Series, within: int) -> int:
    """Number of actual reversing trade pairs separated by <= within sessions."""
    switch_idx = np.flatnonzero(transitions(position).to_numpy())
    return int((np.diff(switch_idx) <= within).sum())


def brief_breaches(raw: pd.Series, within: int) -> int:
    """Raw bearish crossings with recovery within N subsequent closes."""
    values = raw.dropna().to_numpy()
    starts = np.flatnonzero((values[1:] == 0) & (values[:-1] == 1)) + 1
    count = 0
    for j in starts:
        recoveries = np.flatnonzero(values[j+1:] == 1)
        if len(recoveries) and int(recoveries[0]) + 1 <= within:
            count += 1
    return count


def event_rows(r: pd.Series, p: pd.Series, baseline_r: pd.Series,
               baseline_p: pd.Series, calendar: pd.DatetimeIndex):
    """Same-date crash exposure and returns; event wealth rebased at entry close."""
    result = []
    for event, (start, end) in REGIMES.items():
        ix = r.index[(r.index >= pd.Timestamp(start)) &
                     (r.index <= pd.Timestamp(end))]
        if len(ix) == 0:
            continue
        seg, bseg = r.loc[ix], baseline_r.loc[ix]
        pos, bpos = p.loc[ix], baseline_p.loc[ix]
        nav = np.r_[1., (1 + seg).cumprod().to_numpy()]
        peaks = np.maximum.accumulate(nav)
        extra_3x = (pos == 1) & (bpos == 0)
        log_diff = np.log1p(seg) - np.log1p(bseg)
        first_sell = ix[(transitions(p) & p.eq(0)).reindex(ix)]
        result.append(dict(
            event=event, event_start=str(ix[0].date()), event_end=str(ix[-1].date()),
            return_multiple=float(nav[-1]),
            baseline_return_multiple=float((1 + bseg).prod()),
            event_max_drawdown=float((nav / peaks - 1).min()),
            cumulative_log_advantage_vs_base=float(log_diff.sum()),
            extra_3x_days_vs_base=int(extra_3x.sum()),
            extra_1x_days_vs_base=int(((pos == 0) & (bpos == 1)).sum()),
            adverse_extra_3x_log_gap=float(log_diff.where(extra_3x, 0.).clip(upper=0).sum()),
            first_executed_1x_return_end=str(first_sell[0].date()) if len(first_sell) else "",
            executed_switches=int(transitions(p).reindex(ix).sum())))
    return result


def _report(root: Path, grid: pd.DataFrame, stress: pd.DataFrame,
            cohorts: pd.DataFrame, raw_count: dict) -> None:
    """Generate interpretive markdown from computed results; never hand-fill CAGR."""
    def pct(v): return f"{100 * v:.2f}%"
    def rowline(r):
        return (f"| {r['rule']} | {r['lag']} | {pct(r['cagr'])} | "
                f"{r['cagr_delta_pp']:+.2f} | {pct(r['max_drawdown'])} | "
                f"{1+2*r['leveraged_fraction']:.2f}x | {int(r['switch_count'])} | {int(r['whipsaw_5'])} | "
                f"{r['terminal_multiple']:.1f} |")
    primary = grid[(grid.fund == "UPRO") & (grid.sma_days == PRIMARY_LENGTH) &
                   (grid.switch_cost_bps == PRIMARY_COST)].sort_values(["rule", "lag"])
    user_length = grid[(grid.fund == "UPRO") & (grid.sma_days == 185) &
                       (grid.switch_cost_bps == PRIMARY_COST) & (grid.lag == "LAG2")]
    cases = stress[(stress.fund == "UPRO") & (stress.sma_days == 200) &
                   (stress.switch_cost_bps == 25) & (stress.lag == "LAG2") &
                   (stress.event.isin(["1987", "2000_2002", "2007_2009", "2020", "2022"]))]
    lines = [
        "# SMA confirmation, buffer, and pending-order cancellation", "",
        "**Prespecified falsification**, not a parameter search. Invested returns, funding,",
        "fees, execution calendar, transition-cost convention and frozen source bundles are unchanged.",
        "The report uses historical data ending **2026-09-02**, not the November 2026 selloff.", "",
        "The canonical signal is the *unadjusted S&P 500 price close* against its SMA,",
        "while invested portfolio results use distribution-inclusive total returns.",
        "UPRO/SSO rotate into the 1x S&P and TQQQ into the 1x Nasdaq-100; TQQQ",
        "uses the broad S&P signal, matching the prior cross-index test.", "",
        "## Interpretation of the policies", "",
        "- BASE: close above SMA => 3x, otherwise 1x.",
        "- CONFIRM_BEAR2: require two consecutive bearish closes to reduce to 1x;",
        "  require one bullish close to restore leverage (asymmetric, explicitly).",
        "- BUFFER_EXIT_50BP: reduce to 1x only after close is at least 0.5% below",
        "  SMA; re-lever on a close strictly above SMA. This is an exit-side buffer.",
        "- CANCEL_PENDING_SELL: BASE signal, but cancel an *unexecuted* risk-off",
        "  order when an intermediate close recovers. Applies only to LAG2/3.",
        "- LAG1: signal at close t controls return ending t+1 (idealized execution).",
        "  LAG2/3 delay the signal by one/two **additional** complete sessions.",
        "  Orders are otherwise *not* canceled when price recovers.", "",
        "Every full sleeve switch incurs the specified transaction cost once;",
        "costs are assessed on both sales and repurchases, no fee for holding.",
        "SMA=200 is primary. SMA=185 is the user-requested diagnostic, **not**",
        "a retrospectively optimized moving-average length.", "",
        "## Primary comparison: UPRO, SMA 200, 25 bp per switch", "",
        "| Rule | Lag | CAGR | Δ CAGR pp vs BASE at same lag | Max DD | Mean leverage | Switches | 5-session whipsaws | Wealth multiple |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    lines += [rowline(r) for _, r in primary.iterrows()]
    lines += ["", "## User-length diagnostic: UPRO, SMA 185, LAG2, 25 bp", "",
              "| Rule | Lag | CAGR | Δ CAGR pp | Max DD | Mean leverage | Switches | 5-session whipsaws | Wealth multiple |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    lines += [rowline(r) for _, r in user_length.sort_values("rule").iterrows()]
    lines += ["", "## Raw false breaks (S&P signal)", "",
              "| SMA | Recovered within 1 close | 2 closes | 5 closes | 10 closes |",
              "|---|---:|---:|---:|---:|"]
    for length in LENGTHS:
        count = raw_count[length]
        lines += [f"| {length} | {count[1]} | {count[2]} | {count[5]} | {count[10]} |"]
    lines += ["", "## Stress events: UPRO, SMA 200, LAG2, 25 bp", "",
              "| Event | Rule | Event return | Event max DD | Extra 3x days vs BASE | Log return difference |",
              "|---|---|---:|---:|---:|---:|"]
    for _, row in cases.sort_values(["event", "rule"]).iterrows():
        lines += [f"| {row['event']} | {row['rule']} | {pct(row['return_multiple']-1)} | "
                  f"{pct(row['event_max_drawdown'])} | {int(row['extra_3x_days_vs_base'])} | "
                  f"{row['cumulative_log_advantage_vs_base']:+.3f} |"]
    lines += ["", "## How to read this experiment", "",
              "A lower number of trades or short-lived reversals is **not**, by itself,",
              "evidence of superior timing. Assess CAGR, tail drawdowns, lost protection",
              "during 1987/2000-02/2007-09/2020/2022, and the effect of costs together.",
              "The stress CSV also reports the first executed 1x return-end, days of",
              "extra leverage and their negative contribution to log growth.",
              "The grid uses identical dates across strategies and includes SSO/TQQQ",
              "as secondary families. Historical overlapping periods are not independent.",
              "See `signal_null_model_results.md`: the prior timing edge does not survive",
              "multiplicity correction and is strongly concentrated in a few crash days.",
              "Do not interpret small improvements in this grid as evidence of skill.",
              "LAG1 assumes idealized close-based execution, not a guaranteed executable fill.",
              "No signal uses intraday data; no new market prices are fetched.", ""]
    (root / "reports/sma_whipsaw_results.md").write_text("\n".join(lines) + "\n")


def run(root: Path) -> None:
    """Read offline frozen inputs and write separate outputs; do not touch core files."""
    daily, config = load_inputs(root, offline=True)
    prices = load_price_signals(root, config, offline=True)
    calendar = daily.index
    # Exact matched calendar convention of price_signal_revision, including warm-up.
    common = matched(pd.concat([
        daily[["SP500_1X", "NASDAQ100_1X", "LONG_TREASURY_1X", CASH]],
        level_position(prices["SP500"], calendar, 250, 2),
        level_position(prices["NASDAQ100"], calendar, 250, 2)
    ], axis=1)).index
    raw = {(n, rule): raw_states(prices["SP500"], calendar, n, rule)
           for n in LENGTHS for rule in RULES}
    count = {n: {d: brief_breaches(raw[(n, "BASE")], d) for d in (1, 2, 5, 10)}
             for n in LENGTHS}
    reports = root / "reports"
    reports.mkdir(exist_ok=True)
    rows, positions, returns = [], {}, {}
    for n in LENGTHS:
        for fund, off in FUNDS.items():
            lev = f"{fund}_SPREAD_{SPREAD_BPS}BP"
            for rule in RULES:
                for lag in LAGS:
                    cancel = rule == "CANCEL_PENDING_SELL"
                    if cancel and lag == 1:
                        continue
                    p = executed_states(raw[(n, rule)], lag, cancel).loc[common]
                    if p.isna().any():
                        raise ValueError("SMA warm-up incomplete")
                    gross = select_returns(daily, p, {0: off, 1: lev})
                    for cost in COSTS:
                        net = switching_costs(gross, p, cost)
                        metric = evaluate(net, daily[CASH], calendar, p, cohorts=False)
                        rows.append(dict(fund=fund, signal_index="SP500_PRICE",
                            off_asset=off, sma_days=n, rule=rule, lag=f"LAG{lag}",
                            lag_sessions=lag, switch_cost_bps=cost,
                            spread_bps=SPREAD_BPS, whipsaw_2=whipsaws(p, 2),
                            whipsaw_5=whipsaws(p, 5), whipsaw_10=whipsaws(p, 10),
                            leveraged_fraction=float(p.mean()), **metric))
                        if n == PRIMARY_LENGTH and cost == PRIMARY_COST:
                            positions[(fund, rule, lag)] = p
                            returns[(fund, rule, lag)] = net
    grid = pd.DataFrame(rows)
    keys = ["fund", "sma_days", "lag", "switch_cost_bps"]
    baseline = grid[grid.rule == "BASE"][keys +
        ["cagr", "max_drawdown", "terminal_multiple", "switch_count"]]
    baseline = baseline.rename(columns={k: "base_" + k for k in
        ("cagr", "max_drawdown", "terminal_multiple", "switch_count")})
    grid = grid.merge(baseline, on=keys, how="left", validate="many_to_one")
    grid["cagr_delta_pp"] = (grid.cagr - grid.base_cagr) * 100
    grid["drawdown_delta_pp"] = (grid.max_drawdown - grid.base_max_drawdown) * 100
    grid["switches_avoided"] = grid.base_switch_count - grid.switch_count
    grid.pipe(stable_floats).to_csv(reports / "sma_whipsaw_grid.csv",
                                    index=False, float_format=FLOAT_FORMAT)
    stress = []
    for (fund, rule, lag), r in returns.items():
        base_key = (fund, "BASE", lag)
        p = positions[(fund, rule, lag)]
        for entry in event_rows(r, p, returns[base_key], positions[base_key], calendar):
            stress.append(dict(fund=fund, sma_days=PRIMARY_LENGTH, rule=rule,
                               lag=f"LAG{lag}", switch_cost_bps=PRIMARY_COST, **entry))
    stress = pd.DataFrame(stress)
    stress.pipe(stable_floats).to_csv(reports / "sma_whipsaw_stress.csv",
                                      index=False, float_format=FLOAT_FORMAT)
    _report(root, grid, stress, count)
    output_files = ["sma_whipsaw_grid.csv", "sma_whipsaw_stress.csv",
                    "sma_whipsaw_results.md"]
    manifest = dict(as_of=config["as_of"], primary_sma=PRIMARY_LENGTH,
        lengths=LENGTHS, lags=LAGS, costs_bps=COSTS, spread_bps=SPREAD_BPS,
        input_sha256={
            "config.json": sha(root / "config.json"),
            "data/snapshots/portfolio_sma_inputs.zip": sha(root / "data/snapshots/portfolio_sma_inputs.zip"),
            "data/snapshots/sma_price_inputs.zip": sha(root / "data/snapshots/sma_price_inputs.zip")},
        output_sha256={name: sha(reports / name) for name in output_files},
        runtime=dict(python=platform.python_version(), pandas=pd.__version__,
                     numpy=np.__version__))
    (reports / "sma_whipsaw_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    run(args.root)


if __name__ == "__main__":
    main()
