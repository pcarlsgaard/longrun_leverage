import re
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from letf.lflr_reproduction import (ACTUAL, IMMEDIATE, LADDER, MARKET, PAPER_BUY_AND_HOLD,
                                    PAPER_END, PAPER_LIKE, PAPER_ROTATION, PREFERRED,
                                    REQUIRED, SEQUENCE, SMA_DAYS, TRADABLE, baselines,
                                    decomposition, ladder_table, load, one_at_a_time,
                                    report, signal_states, transition_summary,
                                    transition_table)

ROOT = Path(__file__).resolve().parents[1]


class SpecificationTests(unittest.TestCase):
    def test_ladder_names_are_unique(self):
        self.assertEqual(len({spec.name for spec in LADDER}), len(LADDER))

    def test_every_sequence_step_names_real_rungs(self):
        names = {spec.name for spec in LADDER}
        for start, finish, _ in SEQUENCE:
            self.assertIn(start, names)
            self.assertIn(finish, names)

    def test_each_sequence_step_changes_exactly_one_assumption(self):
        """The ladder only decomposes anything if the rungs differ one at a time."""
        specs = {spec.name: spec for spec in LADDER}
        for start, finish, _ in SEQUENCE:
            differing = [field for field in ('signal', 'lag', 'levered', 'risk_off',
                                             'cost_bps')
                         if getattr(specs[start], field) != getattr(specs[finish], field)]
            self.assertEqual(len(differing), 1, f'{start} -> {finish}: {differing}')

    def test_the_two_endpoints_are_the_paper_and_the_preferred_convention(self):
        specs = {spec.name: spec for spec in LADDER}
        self.assertEqual(specs[PAPER_LIKE].lag, IMMEDIATE)
        self.assertEqual(specs[PAPER_LIKE].signal, 'total_return')
        self.assertEqual(specs[PAPER_LIKE].cost_bps, 0)
        self.assertEqual(specs[PREFERRED].lag, TRADABLE)
        self.assertEqual(specs[PREFERRED].signal, 'price')
        self.assertGreater(specs[PREFERRED].cost_bps, 0)


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)

    def test_window_is_the_intersection_of_the_required_series(self):
        expected = self.inputs.daily.loc[:PAPER_END, list(REQUIRED)].dropna().index
        self.assertTrue(self.inputs.ix.equals(expected))
        self.assertEqual(str(self.inputs.ix[-1].date()), PAPER_END)

    def test_window_opens_in_june_2009_on_the_later_of_the_fund_series(self):
        self.assertEqual(self.inputs.ix[0].year, 2009)
        self.assertEqual(self.inputs.ix[0].month, 6)
        later = max(self.inputs.daily[column].first_valid_index()
                    for column in (ACTUAL, MARKET))
        self.assertEqual(self.inputs.ix[0], later)

    def test_no_required_series_has_a_hole_in_the_window(self):
        self.assertFalse(self.inputs.daily.loc[self.inputs.ix, list(REQUIRED)]
                         .isna().any().any())


class SignalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.states = cls.inputs and signal_states(cls.inputs)

    def test_four_conventions_all_warm_and_binary(self):
        self.assertEqual(len(self.states), 4)
        for key, position in self.states.items():
            self.assertFalse(position.isna().any(), key)
            self.assertTrue(position.isin((0., 1.)).all(), key)

    def test_lag_shifts_the_same_signal_by_one_session(self):
        immediate = self.states['price', IMMEDIATE]
        tradable = self.states['price', TRADABLE]
        np.testing.assert_array_equal(tradable.to_numpy()[1:], immediate.to_numpy()[:-1])

    def test_price_position_reads_the_close_lag_sessions_back(self):
        """The claim the transition audit's dating rests on."""
        calendar = self.inputs.calendar
        levels = self.inputs.price.reindex(calendar)
        average = levels.rolling(SMA_DAYS, min_periods=SMA_DAYS).mean()
        for lag in (IMMEDIATE, TRADABLE):
            position = self.states['price', lag]
            for date in (position.index[0], position.index[len(position) // 2],
                         position.index[-1]):
                source = calendar[calendar.get_loc(date) - lag]
                self.assertEqual(float(position.loc[date]),
                                 float(levels[source] > average[source]), (lag, date))

    def test_the_two_signal_sources_disagree_somewhere(self):
        agreement = self.states['price', TRADABLE].eq(
            self.states['total_return', TRADABLE]).mean()
        self.assertLess(agreement, 1.)
        self.assertGreater(agreement, .8)


class ReproductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.states = signal_states(cls.inputs)
        cls.base = baselines(cls.inputs)
        cls.ladder = ladder_table(cls.inputs, cls.states)

    def test_actual_upro_reproduces_the_published_buy_and_hold(self):
        held = self.base.set_index('strategy')
        for name in ('UPRO_ACTUAL_NAV', 'UPRO_ACTUAL_MARKET'):
            self.assertLess(abs(held.loc[name, 'cagr'] - PAPER_BUY_AND_HOLD), .005, name)

    def test_the_reconstruction_matches_the_fund_it_reconstructs(self):
        held = self.base.set_index('strategy')['cagr']
        self.assertLess(abs(held['UPRO_SYNTHETIC'] - held['UPRO_ACTUAL_NAV']), .005)

    def test_the_paper_like_rung_reproduces_the_published_rotation(self):
        rates = self.ladder.set_index('strategy')['cagr']
        self.assertLess(abs(rates[PAPER_LIKE] - PAPER_ROTATION), .005)

    def test_switching_costs_only_ever_subtract(self):
        rates = self.ladder.set_index('strategy')['cagr']
        self.assertLess(rates[PREFERRED], rates['A2_price_signal'])

    def test_leverage_share_matches_the_signal_not_the_rung(self):
        frame = self.ladder.set_index('strategy')
        for name in (PAPER_LIKE, 'A0m_paper_like_market', 'A1_tradable_execution'):
            self.assertAlmostEqual(frame.loc[name, 'fraction_days_leveraged'],
                                   float(self.states['total_return',
                                                     frame.loc[name, 'lag']].mean()), 12)

    def test_metrics_are_finite_and_sane(self):
        for _, row in self.ladder.iterrows():
            self.assertGreater(row.terminal_multiple, 0)
            self.assertLessEqual(row.max_drawdown, 0)
            self.assertGreaterEqual(row.fraction_days_leveraged, 0)
            self.assertLessEqual(row.fraction_days_leveraged, 1)
            self.assertGreater(row.switches_per_year, 0)


class DecompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.states = signal_states(cls.inputs)
        cls.ladder = ladder_table(cls.inputs, cls.states)
        cls.sequential, cls.marginal, cls.interaction = decomposition(
            cls.inputs, cls.states, cls.ladder)

    def test_sequential_effects_reconstruct_each_step(self):
        rates = self.ladder.set_index('strategy')['cagr']
        for _, row in self.sequential.iterrows():
            self.assertAlmostEqual(row.from_cagr + row.effect, row.to_cagr, 12)
        chain = self.sequential[self.sequential.assumption != 'risk-off asset (branch)']
        self.assertAlmostEqual(chain.effect.sum(),
                               float(rates['A5_synthetic'] - rates[PAPER_LIKE]), 12)

    def test_one_at_a_time_skips_changes_that_change_nothing(self):
        frame = one_at_a_time(self.inputs, self.states,
                              {s.name: s for s in LADDER}[PREFERRED],
                              {'risk_off': 'TBILL_3M_1X'}, 'no-op')
        self.assertTrue(frame.empty)

    def test_interaction_is_the_gap_the_single_changes_miss(self):
        for _, row in self.interaction.iterrows():
            self.assertAlmostEqual(row.sum_of_single_changes
                                   + row.unexplained_by_interaction, row.total_gap, 12)

    def test_the_decomposition_does_not_add_up(self):
        """The finding: no ordering of the assumptions is privileged."""
        self.assertGreater(self.interaction.unexplained_by_interaction.abs().max(), .01)


class TransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.states = signal_states(cls.inputs)
        cls.frame = transition_table(cls.inputs, cls.states)

    def test_three_dates_stand_in_the_documented_order(self):
        for _, row in self.frame.iterrows():
            self.assertLess(row.signal_close, row.first_affected_return)
            self.assertLess(row.execution_close, row.first_affected_return)
            self.assertLessEqual(row.signal_close, row.execution_close)

    def test_the_signal_close_is_lag_sessions_before_the_first_return(self):
        calendar = self.inputs.calendar
        for _, row in self.frame.head(40).iterrows():
            place = calendar.get_loc(pd.Timestamp(row.first_affected_return))
            self.assertEqual(str(calendar[place - int(row.lag)].date()), row.signal_close)

    def test_every_convention_produces_transitions(self):
        self.assertEqual(len(self.frame.groupby(['signal', 'lag'])), 4)

    def test_the_disagreement_flag_matches_the_other_signal(self):
        """`signals_disagree` has to be readable off the other convention itself."""
        for _, row in self.frame.iterrows():
            date = pd.Timestamp(row.first_affected_return)
            own = float(self.states[row.signal, int(row.lag)].loc[date])
            other = float(self.states['price' if row.signal == 'total_return'
                                      else 'total_return', int(row.lag)].loc[date])
            self.assertEqual(row.other_signal_state, other)
            self.assertEqual(bool(row.signals_disagree), own != other)

    def test_summary_separates_entries_from_exits(self):
        summary = transition_summary(self.frame)
        self.assertFalse(summary.empty)
        entries = self.frame[(self.frame.audit_period != '') & (self.frame.into == 'UPRO')]
        self.assertAlmostEqual(float(summary.worst_entry_session.min()),
                               float(entries.upro_return.min()), 12)


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        states = signal_states(cls.inputs)
        cls.base = baselines(cls.inputs)
        cls.ladder = ladder_table(cls.inputs, states)
        cls.sequential, cls.marginal, cls.interaction = decomposition(
            cls.inputs, states, cls.ladder)
        cls.crossovers = transition_table(cls.inputs, states)
        cls.summary = transition_summary(cls.crossovers)

    def render(self, directory):
        report(directory, self.inputs, self.base, self.ladder, self.sequential,
               self.marginal, self.interaction, self.summary, self.crossovers)
        return (directory / 'lflr_reproduction.md').read_text()

    def test_answers_all_seven_questions(self):
        with tempfile.TemporaryDirectory() as directory:
            text = self.render(Path(directory))
        for number in range(1, 8):
            self.assertIn(f'**{number}.', text)

    def test_states_the_window_it_measured(self):
        with tempfile.TemporaryDirectory() as directory:
            text = self.render(Path(directory))
        self.assertIn(str(self.inputs.ix[0].date()), text)
        self.assertIn(PAPER_END, text)

    def test_leaves_no_unfilled_placeholder(self):
        with tempfile.TemporaryDirectory() as directory:
            text = self.render(Path(directory))
        for token in ('{', '}'):
            self.assertNotIn(token, text, token)
        for token in (r'\bnan\b', r'\bNone\b', r'\binf\b'):
            self.assertIsNone(re.search(token, text), token)
