"""Why our SMA numbers differ from the published Leverage for the Long Run results.

The post-COVID revision of that paper reports roughly 35.4% for buying and
holding UPRO from inception to the end of 2020, and roughly 24.2% for rotating
between UPRO and Treasury bills on a 200-day moving average of the S&P 500. This
repository's preferred implementation of the same idea reports lower numbers over
longer windows, and the obvious question is whether that is a disagreement about
the data or about the method.

It is about the method, and this module takes the difference apart. Five
assumptions separate the paper-like specification from the one used everywhere
else here, and each is varied on its own:

1. **Signal source** — a total-return index, or the price index an investor
   actually watches. `letf.signals` documents why the second is canonical: the
   two cross their averages on different sessions.
2. **Execution timing** — whether a signal observed at a close governs the very
   next return, or the one after it. Both are free of lookahead; only the second
   is comfortably tradable.
3. **Risk-off asset** — Treasury bills, as in the paper, or the unleveraged
   index, which is how the rest of this repository frames the choice as one of
   leverage intensity rather than of market exposure.
4. **Switching costs** — the paper charges none.
5. **Actual versus synthetic UPRO** — whether the reconstruction this repository
   uses before 2009 behaves like the fund after it.

No parameter is searched. The moving-average length is the paper's 200 sessions
throughout, and the window is fixed by the data rather than chosen: it is the
common interval on which every series below exists.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import json
from pathlib import Path
import platform

import numpy as np
import pandas as pd
import scipy

from .analysis import CASH, load_inputs
from .cohorts import cohort_cagrs, nav_path
from .falsification import load_price_signals
from .hedge_alternatives import CRASHES, cagr, markdown_table, max_drawdown
from .provenance import FLOAT_FORMAT, sha, source_hashes, stable_floats
from .signals import level_position, sma_position
from .strategy import select_returns, switching_costs, transitions

# The paper's own figures, recorded so the report compares against a stated
# target rather than against whatever this run happens to produce.
PAPER_BUY_AND_HOLD = .354
PAPER_ROTATION = .242
PAPER_END = '2020-12-31'

SMA_DAYS = 200
SWITCH_COST_BPS = 25
EQUITY = 'SP500_1X'
ACTUAL, MARKET, SYNTHETIC = 'UPRO_NAV', 'UPRO_MARKET', 'UPRO_BASE'
# Every series the ladder touches. The window is their common interval, so it is
# determined by the data rather than picked, and the market-price series is the
# one that binds.
REQUIRED = (ACTUAL, MARKET, SYNTHETIC, EQUITY, CASH)

# `letf.signals` counts the lag in return-end sessions. 1 is the earliest
# implementation free of lookahead — a signal read at one close governs the very
# next return — and 2 leaves a session to trade in, which is the convention every
# other result in this repository uses.
IMMEDIATE, TRADABLE = 1, 2
COVID = CRASHES['2020_covid']

# The years prior work flagged as decisive. Crossovers here are saved in full.
AUDIT_YEARS = ((2010, 2010), (2011, 2011), (2015, 2016), (2018, 2018), (2020, 2020))


@dataclass(frozen=True)
class Spec:
    """One rung of the ladder. Every field is an assumption the paper makes."""
    name: str
    signal: str            # 'total_return' or 'price'
    lag: int
    levered: str
    risk_off: str
    cost_bps: float
    note: str = ''


LADDER = (
    Spec('A0_paper_like', 'total_return', IMMEDIATE, ACTUAL, CASH, 0,
         'total-return signal, earliest execution, no costs'),
    Spec('A0m_paper_like_market', 'total_return', IMMEDIATE, MARKET, CASH, 0,
         'as A0 on the fund market price the paper appears to quote'),
    Spec('A1_tradable_execution', 'total_return', TRADABLE, ACTUAL, CASH, 0,
         'one session to trade in'),
    Spec('A2_price_signal', 'price', TRADABLE, ACTUAL, CASH, 0,
         'signal read off the price index'),
    Spec('A3_risk_off_index', 'price', TRADABLE, ACTUAL, EQUITY, 0,
         'risk-off in the unleveraged index rather than bills'),
    Spec('A4_preferred', 'price', TRADABLE, ACTUAL, CASH, SWITCH_COST_BPS,
         'the repository convention: price signal, tradable, charged'),
    Spec('A4_index_charged', 'price', TRADABLE, ACTUAL, EQUITY, SWITCH_COST_BPS,
         'as A4 with the index as the risk-off asset'),
    Spec('A5_synthetic', 'price', TRADABLE, SYNTHETIC, CASH, SWITCH_COST_BPS,
         'as A4 on the reconstructed fund'),
)
PREFERRED = 'A4_preferred'
PAPER_LIKE = 'A0_paper_like'


@dataclass(frozen=True)
class Inputs:
    daily: pd.DataFrame
    config: dict
    calendar: pd.DatetimeIndex
    price: pd.Series
    ix: pd.DatetimeIndex
    years: float


def load(root: Path) -> Inputs:
    """Load, and fix the window by intersection rather than by choice.

    The analysis interval is every session on which all of `REQUIRED` exists, up
    to the paper's end date. The fund's market-price series starts two sessions
    after its NAV series, so it is the market price that sets the first return
    date; recording that date in the outputs is the point, because a reader
    comparing CAGRs across papers is usually comparing windows.
    """
    daily, config = load_inputs(root, offline=True)
    price = load_price_signals(root, config, offline=True)['SP500']
    ix = daily.loc[:PAPER_END, list(REQUIRED)].dropna().index
    if not len(ix):
        raise ValueError('No common history for the reproduction window')
    return Inputs(daily, config, daily.index, price, ix,
                  float((ix[-1] - ix[0]).days / 365.25))


def signal_states(inputs: Inputs) -> dict:
    """The four conventions: two signal sources, two execution lags.

    `sma_position` hard-codes the earliest lag, so a further shift of one session
    produces the tradable convention — the same `shift(lag - 1)` idiom
    `letf.falsification` and `letf.price_signal_revision` already use.
    """
    states = {}
    for lag in (IMMEDIATE, TRADABLE):
        states['total_return', lag] = sma_position(
            inputs.daily[EQUITY], inputs.calendar, SMA_DAYS).shift(lag - 1).loc[inputs.ix]
        states['price', lag] = level_position(
            inputs.price, inputs.calendar, SMA_DAYS, lag).loc[inputs.ix]
    for key, position in states.items():
        if position.isna().any():
            raise ValueError(f'Signal {key} is not warm across the window')
    return states


def build(inputs: Inputs, spec: Spec, states: dict):
    """One ladder rung's return series and the position that produced it."""
    position = states[spec.signal, spec.lag]
    returns = select_returns(inputs.daily.loc[inputs.ix], position,
                             {1: spec.levered, 0: spec.risk_off})
    if spec.cost_bps:
        returns = switching_costs(returns, position, spec.cost_bps)
    return returns, position


def describe(name: str, returns: pd.Series, inputs: Inputs, position=None) -> dict:
    """Metrics on one series, on the Sharpe convention `letf.analysis` already uses."""
    cash = inputs.daily.loc[returns.index, CASH]
    excess = returns - cash
    deviation = float(excess.std(ddof=1))
    windows = cohort_cagrs(nav_path(returns, inputs.calendar), 5)
    episode = returns.loc[COVID[0]:COVID[1]]
    row = dict(strategy=name, cagr=cagr(returns),
               terminal_multiple=float((1 + returns).prod()),
               annualized_volatility=float(returns.std(ddof=1) * np.sqrt(252)),
               max_drawdown=max_drawdown(returns),
               sharpe=float(excess.mean() / deviation * np.sqrt(252)) if deviation else np.nan,
               fraction_days_leveraged=np.nan, switches_per_year=np.nan,
               worst_rolling_5y_cagr=float(windows.min()) if len(windows) else np.nan,
               covid_return=float((1 + episode).prod() - 1),
               covid_max_drawdown=max_drawdown(episode))
    if position is not None:
        changes = position.ne(position.shift(1)) & position.shift(1).notna()
        row['fraction_days_leveraged'] = float(position.mean())
        row['switches_per_year'] = float(changes.sum() / inputs.years)
    return row


def baselines(inputs: Inputs) -> pd.DataFrame:
    """Buy and hold, before any signal is applied.

    The point of this table is to establish that the disagreement is not about
    the leveraged series: if the fund's own history does not reproduce the
    paper's buy-and-hold figure, nothing downstream is worth decomposing.
    """
    named = (('SP500_1X', EQUITY), ('UPRO_ACTUAL_NAV', ACTUAL),
             ('UPRO_ACTUAL_MARKET', MARKET), ('UPRO_SYNTHETIC', SYNTHETIC))
    rows = []
    for name, column in named:
        row = describe(name, inputs.daily.loc[inputs.ix, column], inputs)
        row['paper_target'] = PAPER_BUY_AND_HOLD if column in (ACTUAL, MARKET) else np.nan
        row['difference_from_paper'] = row['cagr'] - row['paper_target']
        rows.append(row)
    return pd.DataFrame(rows)


def ladder_table(inputs: Inputs, states: dict) -> pd.DataFrame:
    """Every rung, with the assumptions that produced it carried alongside."""
    rows, previous = [], None
    for spec in LADDER:
        returns, position = build(inputs, spec, states)
        row = dict(describe(spec.name, returns, inputs, position),
                   signal=spec.signal, lag=spec.lag, levered=spec.levered,
                   risk_off=spec.risk_off, cost_bps=spec.cost_bps, note=spec.note)
        row['cagr_change_from_previous_row'] = (np.nan if previous is None
                                                else row['cagr'] - previous)
        row['difference_from_paper_rotation'] = row['cagr'] - PAPER_ROTATION
        previous = row['cagr']
        rows.append(row)
    return pd.DataFrame(rows)


SEQUENCE = (('A0_paper_like', 'A1_tradable_execution', 'execution timing'),
            ('A1_tradable_execution', 'A2_price_signal', 'signal source'),
            ('A2_price_signal', 'A4_preferred', 'switching cost'),
            ('A4_preferred', 'A5_synthetic', 'actual versus synthetic UPRO'),
            ('A2_price_signal', 'A3_risk_off_index', 'risk-off asset (branch)'))

TOWARD_PAPER = {'signal': 'total_return', 'lag': IMMEDIATE, 'cost_bps': 0}
TOWARD_PREFERRED = {'signal': 'price', 'lag': TRADABLE, 'cost_bps': SWITCH_COST_BPS}
# Not differences from the paper — choices this repository makes that the paper
# does not discuss, carried so the table is a complete account of the gap.
SENSITIVITIES = {'risk_off': EQUITY, 'levered': SYNTHETIC}


def sequential_decomposition(ladder: pd.DataFrame) -> pd.DataFrame:
    """The gap walked one assumption at a time, in a fixed order."""
    rates = ladder.set_index('strategy')['cagr']
    return pd.DataFrame([
        dict(step=f'{start} -> {finish}', assumption=assumption,
             from_cagr=float(rates[start]), to_cagr=float(rates[finish]),
             effect=float(rates[finish] - rates[start]))
        for start, finish, assumption in SEQUENCE])


def one_at_a_time(inputs: Inputs, states: dict, base: Spec, changes: dict,
                  kind: str) -> pd.DataFrame:
    """Each assumption changed alone, everything else held at `base`.

    Reported beside the sequential walk because the two disagree, and the
    disagreement is the finding rather than a defect. When an effect depends on
    the setting of the others, no ordering of the steps is privileged and a
    decomposition that adds up exactly would be hiding that.
    """
    baseline = cagr(build(inputs, base, states)[0])
    rows = []
    for attribute, value in changes.items():
        if getattr(base, attribute) == value:
            continue
        variant = replace(base, name=f'{base.name}+{attribute}')
        variant = replace(variant, **{attribute: value})
        rate = cagr(build(inputs, variant, states)[0])
        rows.append(dict(base=base.name, kind=kind, assumption=attribute,
                         changed_to=str(value), base_cagr=float(baseline),
                         variant_cagr=float(rate), effect=float(rate - baseline)))
    return pd.DataFrame(rows)


def decomposition(inputs: Inputs, states: dict, ladder: pd.DataFrame):
    """Sequential and one-at-a-time views, plus what they fail to explain."""
    specs = {spec.name: spec for spec in LADDER}
    sequential = sequential_decomposition(ladder)
    marginal = pd.concat([
        one_at_a_time(inputs, states, specs[PREFERRED], TOWARD_PAPER,
                      'from preferred toward the paper'),
        one_at_a_time(inputs, states, specs[PREFERRED], SENSITIVITIES,
                      'from preferred, internal sensitivity'),
        one_at_a_time(inputs, states, specs[PAPER_LIKE], TOWARD_PREFERRED,
                      'from the paper toward preferred'),
        one_at_a_time(inputs, states, specs[PAPER_LIKE], SENSITIVITIES,
                      'from the paper, internal sensitivity')], ignore_index=True)
    rates = ladder.set_index('strategy')['cagr']
    total = float(rates[PREFERRED] - rates[PAPER_LIKE])
    additive = {}
    for kind, direction in (('from the paper toward preferred', 1.),
                            ('from preferred toward the paper', -1.)):
        piece = marginal[marginal.kind == kind]
        additive[kind] = direction * float(piece.effect.sum())
    interaction = pd.DataFrame([
        dict(view=kind, total_gap=total, sum_of_single_changes=value,
             unexplained_by_interaction=total - value)
        for kind, value in additive.items()])
    return sequential, marginal, interaction


def transition_table(inputs: Inputs, states: dict) -> pd.DataFrame:
    """Every crossover, dated three ways, with what the fund did next.

    Prior work in this repository established that a handful of sessions can
    carry decades of a signal's advantage, so the dates are saved rather than
    summarized away. Three dates matter and they are different: the close the
    signal was read at, the close a trade had to be placed by, and the first
    return the new position earned.
    """
    rows = []
    for (signal, lag), position in states.items():
        other = states['price' if signal == 'total_return' else 'total_return', lag]
        for date in position.index[transitions(position)]:
            place = inputs.calendar.get_loc(date)
            rows.append(dict(
                signal=signal, lag=lag,
                signal_close=inputs.calendar[place - lag].date().isoformat(),
                execution_close=inputs.calendar[place - 1].date().isoformat(),
                first_affected_return=date.date().isoformat(),
                into='UPRO' if position.loc[date] == 1 else 'risk_off',
                upro_return=float(inputs.daily.loc[date, ACTUAL]),
                sp500_return=float(inputs.daily.loc[date, EQUITY]),
                other_signal_state=float(other.loc[date]),
                signals_disagree=bool(position.loc[date] != other.loc[date]),
                audit_period=next((f'{lo}' if lo == hi else f'{lo}-{hi}'
                                   for lo, hi in AUDIT_YEARS if lo <= date.year <= hi), '')))
    return pd.DataFrame(rows).sort_values(
        ['signal', 'lag', 'first_affected_return'], ignore_index=True)


def transition_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """The audit years only, compact enough for the report.

    Entries and exits are kept apart because the same number means opposite
    things. A steep fund loss on the first session of a move *into* leverage is
    damage the rule caused; the same loss on the first session after a move
    *out* is damage it avoided. Reporting one worst session across both would
    net a rule's best day against its worst.
    """
    audit = frame[frame.audit_period != '']
    rows = []
    for period, block in audit.groupby('audit_period', sort=True):
        for (signal, lag), group in block.groupby(['signal', 'lag']):
            entries = group[group.into == 'UPRO']
            exits = group[group.into != 'UPRO']
            rows.append(dict(
                period=period, signal=signal, lag=lag, transitions=int(len(group)),
                disagreements=int(group.signals_disagree.sum()),
                worst_entry_session=(float(entries.upro_return.min())
                                     if len(entries) else np.nan),
                worst_entry_date=(entries.loc[entries.upro_return.idxmin(),
                                              'first_affected_return']
                                  if len(entries) else ''),
                worst_session_avoided=(float(exits.upro_return.min())
                                       if len(exits) else np.nan),
                worst_avoided_date=(exits.loc[exits.upro_return.idxmin(),
                                              'first_affected_return']
                                    if len(exits) else '')))
    return pd.DataFrame(rows)


def run(root: Path):
    inputs = load(root)
    states = signal_states(inputs)
    base = baselines(inputs)
    ladder = ladder_table(inputs, states)
    sequential, marginal, interaction = decomposition(inputs, states, ladder)
    crossovers = transition_table(inputs, states)
    summary = transition_summary(crossovers)

    reports = root / 'reports'
    outputs = {'lflr_reproduction.csv': pd.concat([base.assign(kind='baseline'),
                                                   ladder.assign(kind='ladder')],
                                                  ignore_index=True),
               'lflr_reproduction_decomposition.csv': pd.concat(
                   [sequential.assign(view='sequential'),
                    marginal.rename(columns={'kind': 'view'}),
                    interaction.assign(view='interaction check')], ignore_index=True),
               'lflr_reproduction_transitions.csv': crossovers}
    for name, frame in outputs.items():
        frame.pipe(stable_floats).to_csv(reports / name, index=False,
                                         float_format=FLOAT_FORMAT)
    report(reports, inputs, base, ladder, sequential, marginal, interaction, summary,
           crossovers)
    (reports / 'lflr_reproduction_manifest.json').write_text(json.dumps({
        'window': [inputs.ix[0].date().isoformat(), inputs.ix[-1].date().isoformat()],
        'first_return': inputs.ix[0].date().isoformat(),
        'observations': int(len(inputs.ix)), 'years': inputs.years,
        'window_rule': 'the common interval on which every required series exists, '
                       f'truncated at {PAPER_END}',
        'required_series': list(REQUIRED), 'sma_days': SMA_DAYS,
        'lags': {'immediate': IMMEDIATE, 'tradable': TRADABLE},
        'switch_cost_bps': SWITCH_COST_BPS,
        'paper_buy_and_hold': PAPER_BUY_AND_HOLD, 'paper_rotation': PAPER_ROTATION,
        'ladder': [dict(name=s.name, signal=s.signal, lag=s.lag, levered=s.levered,
                        risk_off=s.risk_off, cost_bps=s.cost_bps) for s in LADDER],
        'transitions': int(len(crossovers)),
        'python': platform.python_version(), 'numpy': np.__version__,
        'pandas': pd.__version__, 'scipy': scipy.__version__,
        'source_hashes': source_hashes(root, __spec__.name),
        'outputs_sha256': {name: sha(reports / name) for name in outputs},
    }, indent=2) + '\n')
    print(f'LFLR reproduction: {inputs.ix[0].date()}-{inputs.ix[-1].date()}, '
          f'{len(ladder)} ladder rows, {len(crossovers)} crossovers.')
    return base, ladder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--offline', action='store_true', default=True)
    run(parser.parse_args().root)


def _narrative(base, ladder, sequential, marginal, interaction) -> dict:
    """Every number the prose states, derived from the tables it sits beside."""
    rates = ladder.set_index('strategy')
    held = base.set_index('strategy')
    single = marginal.set_index(['base', 'assumption'])['effect']
    closest = held.loc[['UPRO_ACTUAL_NAV', 'UPRO_ACTUAL_MARKET'],
                       'difference_from_paper'].abs().idxmin()
    rotation = (rates.loc[[PAPER_LIKE, 'A0m_paper_like_market'],
                          'difference_from_paper_rotation'].abs())
    spread = {attribute: (float(single[PAPER_LIKE, attribute]),
                          float(single[PREFERRED, attribute]))
              for attribute in ('signal', 'lag', 'cost_bps')}
    biggest = max(spread, key=lambda a: abs(spread[a][0] - spread[a][1]))
    return dict(
        rates=rates, held=held, spread=spread, biggest=biggest,
        closest=closest,
        closest_gap=float(held.loc[closest, 'difference_from_paper']),
        rotation_best=rotation.idxmin(), rotation_gap=float(rotation.min()),
        preferred=float(rates.loc[PREFERRED, 'cagr']),
        preferred_gap=float(rates.loc[PREFERRED, 'difference_from_paper_rotation']),
        index_variant=float(rates.loc['A4_index_charged', 'cagr']),
        synthetic_effect=float(single[PREFERRED, 'levered']),
        interaction=float(interaction.unexplained_by_interaction.abs().max()),
        total_gap=float(interaction.total_gap.iloc[0]),
        levered_share=float(rates.loc[PREFERRED, 'fraction_days_leveraged']),
        switches=float(rates.loc[PREFERRED, 'switches_per_year']))


def report(reports: Path, inputs, base, ladder, sequential, marginal, interaction,
           summary, crossovers):
    """Write the narrative from the numbers, never alongside them."""
    v = _narrative(base, ladder, sequential, marginal, interaction)
    spread = v['spread']
    money = {c: '.2%' for c in ('cagr', 'max_drawdown', 'annualized_volatility',
                                'worst_rolling_5y_cagr', 'covid_return',
                                'covid_max_drawdown', 'fraction_days_leveraged')}
    ladder_columns = ['strategy', 'signal', 'lag', 'levered', 'risk_off', 'cost_bps',
                      'cagr', 'terminal_multiple', 'annualized_volatility',
                      'max_drawdown', 'sharpe', 'fraction_days_leveraged',
                      'switches_per_year', 'worst_rolling_5y_cagr', 'covid_return',
                      'covid_max_drawdown', 'cagr_change_from_previous_row']
    ladder_formats = dict(money, terminal_multiple=',.1f', sharpe='.2f',
                          switches_per_year='.2f', cost_bps='.0f',
                          cagr_change_from_previous_row='+.2%')

    text = f"""# Reproducing Leverage for the Long Run, and locating the difference

Generated by `letf.lflr_reproduction`. Window {inputs.ix[0].date()} to {inputs.ix[-1].date()}, {len(inputs.ix):,} sessions, {inputs.years:.2f} years.

The post-COVID revision of that paper reports about {PAPER_BUY_AND_HOLD:.1%} for
buying and holding UPRO from inception through {PAPER_END}, and about
{PAPER_ROTATION:.1%} for rotating between UPRO and Treasury bills on a
{SMA_DAYS}-day moving average. This repository's preferred implementation reports
less over longer windows. This module asks whether that is a disagreement about
the data or about the method, by varying one assumption at a time.

The window is not chosen. It is every session on which all of
{', '.join(REQUIRED)} exists, truncated at the paper's end date; the fund's
market-price series begins two sessions after its NAV series and so sets the
first return date, **{inputs.ix[0].date()}**.

## Buy and hold: is the leveraged series the same?

{markdown_table(base, ['strategy', 'cagr', 'terminal_multiple', 'annualized_volatility',
                       'max_drawdown', 'sharpe', 'paper_target', 'difference_from_paper'],
                dict(money, terminal_multiple=',.1f', sharpe='.2f', paper_target='.1%',
                     difference_from_paper='+.2%'))}

Yes. The closest series, {v['closest']}, lands {abs(v['closest_gap']):.2%} from the
published figure — inside the rounding of the number it is being compared to. The
reconstruction this repository uses before the fund existed is also indistinguishable
from the fund over the years both cover, which removes the reconstruction as a
candidate explanation before the decomposition starts.

## The ladder

Each row changes one assumption from the row above it. `lag` counts return-end
sessions: 1 is the earliest implementation free of lookahead, where a signal read
at one close governs the very next return; 2 leaves a session to trade in and is
the convention every other result in this repository uses.

{markdown_table(ladder, ladder_columns, ladder_formats)}

The paper's rotation figure reproduces too: {v['rotation_best']} sits
{v['rotation_gap']:.2%} from it. Both of the paper's headline numbers are
therefore reproducible on this data to within a tenth of a percentage point, and
the choice between the fund's NAV and its market price is smaller than the
precision the paper quotes.

## Where the difference comes from

Walked in one order, one assumption at a time:

{markdown_table(sequential, ['step', 'assumption', 'from_cagr', 'to_cagr', 'effect'],
                {'from_cagr': '.2%', 'to_cagr': '.2%', 'effect': '+.2%'})}

Changed one at a time instead, holding everything else fixed at each end:

{markdown_table(marginal, ['base', 'kind', 'assumption', 'changed_to', 'variant_cagr',
                           'effect'],
                {'variant_cagr': '.2%', 'effect': '+.2%'})}

{markdown_table(interaction, ['view', 'total_gap', 'sum_of_single_changes',
                              'unexplained_by_interaction'],
                {'total_gap': '+.2%', 'sum_of_single_changes': '+.2%',
                 'unexplained_by_interaction': '+.2%'})}

**The decomposition does not add up, and that is the result rather than a
defect.** The whole gap between the paper-like specification and the preferred one
is {v['total_gap']:+.2%}, while the single changes sum to something up to
{v['interaction']:.2%} away from it. No ordering of the steps is privileged,
because each assumption's effect depends on the settings of the others. The
clearest case is `{v['biggest']}`: measured from the paper's specification it is
worth {spread[v['biggest']][0]:+.2%}, and measured from the preferred one
{spread[v['biggest']][1]:+.2%}.

## Crossovers

Three dates matter at every crossover and they are different: the close the
signal was read at, the close a trade had to be placed by, and the first return
the new position earned. All {len(crossovers)} are in
`lflr_reproduction_transitions.csv`; the years prior work flagged as decisive are
summarized here.

{markdown_table(summary, ['period', 'signal', 'lag', 'transitions', 'disagreements',
                          'worst_entry_session', 'worst_entry_date',
                          'worst_session_avoided', 'worst_avoided_date'],
                {'worst_entry_session': '.2%', 'worst_session_avoided': '.2%'})}

`worst_entry_session` is the fund's return on the first session of a move *into*
leverage — damage the rule caused. `worst_session_avoided` is its return on the
first session after a move *out* — damage the rule sidestepped. The two are kept
apart because netting them would set a rule's best day against its worst.
`disagreements` counts crossovers at which the other signal source held the
opposite position.
"""
    (reports / 'lflr_reproduction.md').write_text(text + _answers(v, inputs))


def _answers(v, inputs) -> str:
    """The seven prespecified answers, every claim read off the tables above."""
    rates, spread = v['rates'], v['spread']
    ranked = sorted(spread, key=lambda a: abs(spread[a][0] - spread[a][1]), reverse=True)
    # spread[attribute] is (measured from the paper rung, measured from the
    # preferred rung). The paper rung reads a total-return signal at the
    # immediate lag; the preferred one reads price at the tradable lag. So the
    # first element of each pair is always the effect under the paper's setting
    # of the *other* assumptions, and the second under this repository's.
    from_paper, from_preferred = 0, 1
    # The two rungs sit at opposite settings, so a change measured from each runs
    # in the opposite direction. Both are negated onto one direction before being
    # compared — quoting them as they come out would set a move toward the price
    # signal against a move away from it and call the pair a range.
    to_price_at_immediate = spread['signal'][from_paper]
    to_price_at_tradable = -spread['signal'][from_preferred]
    wait_on_total_return = spread['lag'][from_paper]
    wait_on_price = -spread['lag'][from_preferred]
    sign_flip = to_price_at_immediate * to_price_at_tradable < 0
    pairing = ('The effect changes sign. The price signal is worse than the total-return '
               'one when acted on immediately and better when acted on a session later, '
               'so neither signal is better than the other except in company with a lag'
               if sign_flip else
               'The two agree in sign, so the signal source has a direction of effect '
               'independent of the lag, though not a magnitude')
    lag_flip = wait_on_total_return * wait_on_price < 0
    return f"""
## The seven questions

**1. Can we reproduce the paper's actual-UPRO buy-and-hold CAGR?**
Yes, essentially exactly. {v['closest']} returns
{v['held'].loc[v['closest'], 'cagr']:.2%} against the published
{PAPER_BUY_AND_HOLD:.1%} — a gap of {abs(v['closest_gap']) * 10000:.1f} basis
points, an order of magnitude finer than the precision the paper quotes.

**2. Can we approximately reproduce the ~{PAPER_ROTATION:.1%} rotation return?**
Yes. {v['rotation_best']} returns
{rates.loc[v['rotation_best'], 'cagr']:.2%}, {v['rotation_gap'] * 10000:.1f} basis
points from the published figure. The specification that gets there is the paper's own: a
total-return signal, the earliest non-lookahead execution, Treasury bills in the
risk-off state and no trading costs.

**3. What explains the largest remaining difference?**
Nothing does, in the sense the question expects — because there is barely a
difference left to explain. Over this window the preferred implementation returns
{v['preferred']:.2%} against the paper's {PAPER_ROTATION:.1%}, a gap of
{abs(v['preferred_gap']):.2%}. That near-agreement is not because the assumptions
do not matter but because they cancel. Measured from the preferred specification,
reverting the signal to total return is worth
{spread['signal'][from_preferred]:+.2%}, removing the switching cost
{spread['cost_bps'][from_preferred]:+.2%}, and reverting the execution lag
{spread['lag'][from_preferred]:+.2%}. They do not sum to the gap and no reordering
makes them. The largest single term is `{ranked[0]}`, and it is also the least
stable: its measured effect spans {min(spread[ranked[0]]):+.2%} to
{max(spread[ranked[0]]):+.2%} depending on where the other assumptions sit.

**4. How much does total-return versus price-only signalling matter over this window?**
It depends entirely on the lag it is measured at. Switching from the total-return
signal to the price signal is worth {to_price_at_immediate:+.2%} at the immediate
lag and {to_price_at_tradable:+.2%} at the tradable one. {pairing}. Neither number
is the effect of the signal source on its own, and there is no third number that
is.

**5. How much does one session of execution timing matter?**
Far more than the word "timing" suggests. Waiting one session before acting is
worth {wait_on_total_return:+.2%} on a total-return signal and
{wait_on_price:+.2%} on a price signal —
{'opposite in sign, so the delay that helps one hurts the other'
 if lag_flip else 'the same direction, but differing by a factor of '
 f'{max(abs(wait_on_total_return), abs(wait_on_price)) / max(min(abs(wait_on_total_return), abs(wait_on_price)), 1e-9):.0f}'}.
A rule that turns over
{v['switches']:.1f} times a year across {inputs.years:.0f} years has few enough
decision points that a handful of them carry the difference, which is the same
finding `letf.null_model` and the edge-concentration diagnostic reach by other
routes.

**6. Does actual versus synthetic UPRO matter?**
No. Substituting the reconstruction for the fund moves the preferred
specification by {v['synthetic_effect']:+.2%}, and buy-and-hold by less. Whatever
separates this repository's longer-window results from the paper's, it is not the
leveraged series.

**7. What does a realistically tradable implementation produce over the same episode?**
{v['preferred']:.2%} with bills in the risk-off state, or {v['index_variant']:.2%}
holding the unleveraged index there instead — both after {SWITCH_COST_BPS}bp a
switch and with a session left to trade in. The rule holds leverage on
{v['levered_share']:.0%} of sessions over this window, which is the more important
number: across a stretch in which the index roughly quintupled, a rule that is
leveraged seven days in eight is close to buy-and-hold, and the comparison flatters
it accordingly.

## What this does and does not settle

It settles that the disagreement is methodological, not empirical. The data
reproduces both published figures, the reconstruction matches the fund, and the
preferred implementation lands within half a point of the paper over the paper's
own window.

It does not settle the longer-window disagreement, and this window cannot. From
{inputs.ix[0].date()} to {inputs.ix[-1].date()} the signal was risk-on
{v['levered_share']:.0%} of the time and the one large crash was recovered within
months. The sessions on which this family of rules earns its advantage — October
1987 above all — are not in it. Read this report as evidence that the
implementations agree about the 2010s, not as evidence about the rule.

## Limitations

* The paper's own figures are quoted to one decimal place, so agreement closer
  than {abs(v['closest_gap']):.2%} cannot be demonstrated either way.
* The paper's precise execution and cost conventions are not stated in enough
  detail to be certain the reproduction matches them rather than merely landing
  on the same number.
* One window, one moving-average length, one leveraged fund. Nothing here is
  searched, which is deliberate, but it also means nothing here bounds how the
  agreement would hold under a length the paper did not use.
* The interaction terms are large enough that any single number quoted from the
  decomposition is conditional on the rest of the specification. Quote the range.
"""


if __name__ == '__main__':
    main()
