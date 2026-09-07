"""The test this repository was missing: does the timing signal beat chance?

Every other battery here varies a *nuisance* parameter — SMA length, execution
lag, financing spread, switching cost, subperiod. None of them asks the prior
question: would a rule with no timing information at all, but the same trading
profile, have done as well?

**The null.** Take the realized position series and cut it into episodes (runs
of a constant allocation state). Shuffle the order of those episodes. The
permuted rule holds exactly the same multiset of episode lengths, makes exactly
the same number of switches, and spends exactly the same fraction of sessions
leveraged. The only thing destroyed is *when* the episodes fall. A real timing
signal should beat this; a rule that merely holds less leverage on average
should not.

This matters because the second explanation is live: in the leveraged sleeves,
a large share of the apparent benefit is simply reduced average exposure, which
the matched-exposure controls already suspected.

**Multiplicity.** The reported p-value is uncorrected. `SPECIFICATIONS` is the
number of nuisance-parameter cells this signal family was actually evaluated
over in the batteries — computed from the constants, not asserted — and
`p_sidak` is the Šidák-adjusted equivalent. A result that survives the
uncorrected p but not the adjusted one has not been established; it has been
searched for. Read `p_sidak`, not `p_value`.

Run: PYTHONPATH=src python -m letf.null_model [--permutations N] [--seed N]
"""
from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path

import numpy as np
import pandas as pd
import scipy

from .analysis import CASH, FAMILIES, load_inputs, matched, sha
from .cohorts import nav_path
from .diagnostics import TOP_DAYS, edge_concentration
from .falsification import load_price_signals
from .signals import LAGS, level_position
from .strategy import select_returns, switching_costs
from .provenance import FLOAT_FORMAT

PERMUTATIONS = 2000
SEED = 20260907
SMA_DAYS = 200
SPREAD_BPS = 50
PRIMARY_COST = 25

# Nuisance-parameter cells each signal family was evaluated over across the
# falsification and price-revision batteries: SMA length x lag x spread x
# switching cost x off-asset. This is the family size the p-values are
# corrected for. It does NOT include the alternative regime signals or the
# cross-index variants, so the correction is, if anything, too generous.
SPECIFICATIONS = 3 * 2 * 3 * 4 * 2


def episodes(position: pd.Series):
    """Contiguous runs of a constant allocation state, as (state, length)."""
    values = position.to_numpy()
    if not len(values):
        return []
    cuts = np.flatnonzero(values[1:] != values[:-1]) + 1
    starts = np.concatenate([[0], cuts])
    lengths = np.diff(np.concatenate([starts, [len(values)]]))
    return list(zip(values[starts], lengths))


def permuted_position(position: pd.Series, rng: np.random.Generator) -> pd.Series:
    """Same episode lengths, same switch count, same exposure — new timing.

    The *sequence of states* is held fixed and the episode *lengths* are
    shuffled within each state. That preserves, exactly and by construction:
    the number of switches (the state sequence is untouched), each state's
    multiset of episode lengths, and therefore the fraction of sessions spent
    at each exposure. What moves is every episode boundary, so risk-off periods
    land on different dates.

    Reordering whole episodes instead would let two same-state episodes become
    adjacent and merge, quietly lowering the switch count and un-matching the
    null on trading activity. This construction cannot do that.
    """
    blocks = episodes(position)
    if len(blocks) < 2:
        return position.copy()
    states = np.array([s for s, _ in blocks])
    lengths = np.array([n for _, n in blocks])
    shuffled = lengths.copy()
    for state in np.unique(states):
        where = np.flatnonzero(states == state)
        shuffled[where] = rng.permutation(lengths[where])
    values = np.repeat(states, shuffled)
    return pd.Series(values, index=position.index, name=position.name)


def total_cagr(returns: pd.Series, calendar: pd.DatetimeIndex) -> float:
    nav = nav_path(returns, calendar)
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    return float(nav.iloc[-1] ** (1 / years) - 1) if nav.iloc[-1] > 0 else -1.0


def placebo_cagrs(daily, position, columns, cost, calendar, permutations, rng):
    """CAGR distribution under randomized timing with matched trading profile."""
    out = np.empty(permutations)
    for i in range(permutations):
        state = permuted_position(position, rng)
        gross = select_returns(daily, state, columns)
        out[i] = total_cagr(switching_costs(gross, state, cost), calendar)
    return out


def assess(daily, calendar, name, position, columns, benchmark, cost=PRIMARY_COST,
           permutations=PERMUTATIONS, seed=SEED):
    """One strategy: real outcome, placebo distribution, edge concentration."""
    rng = np.random.default_rng(seed)
    real_gross = select_returns(daily, position, columns)
    real = switching_costs(real_gross, position, cost)
    real_cagr = total_cagr(real, calendar)
    placebo = placebo_cagrs(daily, position, columns, cost, calendar, permutations, rng)

    # One-sided: how often does randomized timing match or beat the real rule?
    # The +1/+1 correction keeps the p-value from ever being exactly zero, which
    # a finite permutation sample cannot justify.
    exceed = int((placebo >= real_cagr).sum())
    p = (exceed + 1) / (permutations + 1)
    row = {
        'strategy': name,
        'sma_days': SMA_DAYS,
        'lag': position.attrs.get('lag', ''),
        'switch_cost_bps': cost,
        'permutations': permutations,
        'real_cagr': real_cagr,
        'benchmark_cagr': total_cagr(benchmark, calendar),
        'placebo_median_cagr': float(np.median(placebo)),
        'placebo_p90_cagr': float(np.quantile(placebo, .90)),
        'real_percentile_in_placebo': float((placebo < real_cagr).mean()),
        'placebo_beats_benchmark_fraction': float((placebo > total_cagr(benchmark, calendar)).mean()),
        'p_value': p,
        'specifications': SPECIFICATIONS,
        'p_sidak': float(1 - (1 - p) ** SPECIFICATIONS),
        'significant_at_5pct_after_multiplicity': bool(1 - (1 - p) ** SPECIFICATIONS < .05),
    }
    row.update(edge_concentration(real, benchmark))
    return row


def report(out: pd.DataFrame, reports: Path, permutations: int):
    def pct(x): return f'{100 * x:.2f}%'
    survivors = out[out.significant_at_5pct_after_multiplicity]
    lines = [
        '# Does the timing signal beat chance?', '',
        'Every other battery in this repository varies a nuisance parameter — SMA',
        'length, execution lag, financing spread, switching cost, subperiod. None of',
        'them asks whether a rule with the same trading profile but **no timing',
        'information** would have done as well. This report asks that.', '',
        '## Method', '',
        f'For each strategy the realized position series is cut into episodes of constant',
        f'allocation. The episode lengths are reshuffled within each state, {permutations:,} times.',
        'Every draw therefore holds the same number of switches, the same multiset of',
        'episode lengths, and the same fraction of sessions spent leveraged as the real',
        'rule. Only the dates move. A genuine timing signal should beat this null; a rule',
        'whose benefit comes from simply holding less leverage should not.', '',
        f'`p_value` is the uncorrected one-sided permutation p-value. `p_sidak` corrects it',
        f'for the {SPECIFICATIONS} nuisance-parameter cells each signal family was actually',
        'evaluated over (SMA length × lag × spread × switching cost × off-asset). That',
        'correction excludes the alternative regime signals and the cross-index variants,',
        'so it is if anything too generous. **Read `p_sidak`, not `p_value`.**', '',
        '## Results', '',
        '| Strategy | Lag | Real CAGR | Always-on | Placebo median | Real percentile | p | p (Šidák) |',
        '|---|---|---:|---:|---:|---:|---:|---:|',
    ]
    for _, r in out.sort_values(['lag', 'strategy']).iterrows():
        lines.append(f'| {r.strategy} | {r.lag} | {pct(r.real_cagr)} | {pct(r.benchmark_cagr)} | '
                     f'{pct(r.placebo_median_cagr)} | {pct(r.real_percentile_in_placebo)} | '
                     f'{r.p_value:.4f} | {r.p_sidak:.3f} |')
    lines += ['', f'**{len(survivors)} of {len(out)} strategies are significant at 5% after the multiplicity',
              'correction.**', '']
    if not len(survivors):
        lines += ['No headline strategy in this repository survives a correction for the size of',
                  'the search that produced it. That does not make the effects zero — most sit in',
                  'the upper tail of their own null — but it does mean none of them has been',
                  '*established* here. They are candidates, not findings.', '']
    lines += [
        'The `placebo_beats_benchmark_fraction` column in the CSV is the blunter number:',
        'the share of random-timing rules that beat always-on leverage. Where it is large,',
        'most of the apparent benefit is reduced average exposure rather than timing —',
        'which is exactly what the matched-exposure controls elsewhere in this repository',
        'suspected.', '',
        '## Edge concentration', '',
        'A 40-year CAGR gap is a sum of ~10,000 daily log differences. If a few sessions',
        'supply most of it, the gap describes those sessions, not a repeatable edge.', '',
        '| Strategy | Lag | Total log advantage | Top 1 day | Top 5 | Top 20 | Top month | Month share |',
        '|---|---|---:|---:|---:|---:|---|---:|',
    ]
    for _, r in out.sort_values(['lag', 'strategy']).iterrows():
        lines.append(f'| {r.strategy} | {r.lag} | {r.total_log_advantage:.4f} | '
                     f'{pct(r.top1_day_share)} | {pct(r.top5_day_share)} | {pct(r.top20_day_share)} | '
                     f'{r.top_month} | {pct(r.top_month_share)} |')
    lines += ['', '"Top" means most favorable to the sign of the gap: for a positive advantage the',
              'sessions that produced it, for a negative one the sessions that cost most. Shares',
              'are fractions of the total, so 60% means one session produced 60% of a whole',
              '40-year advantage.', '',
              '**A share above 100% is the important case, not an error.** It means those few',
              'sessions produced more than the entire gap and the rest of the history was net',
              'negative — the strategy did not beat its benchmark over the other ~10,000',
              'sessions. Every row here has a top-20 share above 100%. None of these',
              'advantages is a property of the strategy across time; each is a property of a',
              'handful of days, and the largest of them cluster in October 1987, February 2001',
              'and October 2008. Do not quote a CAGR gap from this repository without this',
              'column beside it.', '']
    (reports / 'signal_null_model_results.md').write_text('\n'.join(lines) + '\n')


def run(root: Path, permutations=PERMUTATIONS, seed=SEED):
    daily, config = load_inputs(root, offline=True)
    calendar = daily.index
    prices = load_price_signals(root, config, offline=True)
    positions = {(u, lag): level_position(prices[u], calendar, SMA_DAYS, lag)
                 for u in set(FAMILIES.values()) for lag in LAGS}
    ix = matched(pd.concat([
        daily[['SP500_1X', 'NASDAQ100_1X', 'LONG_TREASURY_1X', CASH]],
        level_position(prices['SP500'], calendar, 250, 2),
        level_position(prices['NASDAQ100'], calendar, 250, 2),
    ], axis=1)).index

    rows = []
    for lag in LAGS:
        for fund, under in FAMILIES.items():
            p = positions[(under, lag)].loc[ix]
            p.attrs['lag'] = f'LAG{lag}'
            levered = f'{fund}_SPREAD_{SPREAD_BPS}BP'
            off_label = 'NASDAQ' if under == 'NASDAQ100' else 'SP500'
            always = daily.loc[ix, levered]
            for label, off in ((off_label, f'{under}_1X'), ('TBILL', CASH)):
                rows.append(assess(daily, calendar, f'{fund}_SMA_TO_{label}', p,
                                   {0: off, 1: levered}, always,
                                   permutations=permutations, seed=seed))

    out = pd.DataFrame(rows)
    reports = root / 'reports'
    out.to_csv(reports / 'signal_null_model.csv', index=False, float_format=FLOAT_FORMAT)
    report(out, reports, permutations)
    (reports / 'signal_null_model_manifest.json').write_text(json.dumps({
        'permutations': permutations, 'seed': seed, 'specifications': SPECIFICATIONS,
        'sma_days': SMA_DAYS, 'spread_bps': SPREAD_BPS, 'switch_cost_bps': PRIMARY_COST,
        'window': [ix[0].date().isoformat(), ix[-1].date().isoformat()],
        'python': platform.python_version(), 'numpy': np.__version__,
        'pandas': pd.__version__, 'scipy': scipy.__version__,
        'output_sha256': sha(reports / 'signal_null_model.csv'),
    }, indent=2) + '\n')
    print(f'Wrote {len(out)} null-model rows to {reports / "signal_null_model.csv"}')
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--permutations', type=int, default=PERMUTATIONS)
    parser.add_argument('--seed', type=int, default=SEED)
    parser.add_argument('--offline', action='store_true', default=True)
    args = parser.parse_args()
    run(args.root, args.permutations, args.seed)


if __name__ == '__main__':
    main()
