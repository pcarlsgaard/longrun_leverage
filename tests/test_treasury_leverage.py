import re
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from letf.hedge_alternatives import CRASHES, TREASURY, constant_leverage
from letf.leaps_robustness import PRIMARY_BLOCK, SEED, STRUCTURES, WARMUP_SESSIONS
from letf.treasury_leverage import (JOINT_WINDOWS, LEVERAGES, ROLL_LABEL, SHOCK_OFFSETS,
                                    SHOCK_REPEATS, SLOT, STRESS_LEVERAGE, bond_financing,
                                    build_horizon, daily_reset, exchange_rates, figure,
                                    frontier_table, historical_frontier,
                                    joint_loss, label, load, monte_carlo, report,
                                    rolling_compound, run_path, shock_path, shock_sample,
                                    shock_table, short, summarize, treasury_pool,
                                    treasury_sleeve)

ROOT = Path(__file__).resolve().parents[1]


class NamingTests(unittest.TestCase):
    def test_structure_names_lose_only_the_sleeve_suffix(self):
        self.assertEqual(short('LEAPS_85_30_TREASURY'), 'LEAPS_85_30')
        self.assertEqual(short('LEAPS_85_30'), 'LEAPS_85_30')

    def test_labels_are_distinct_across_leverages(self):
        names = {label(s, v) for s in STRUCTURES for v in LEVERAGES + (STRESS_LEVERAGE,)}
        self.assertEqual(len(names), len(STRUCTURES) * (len(LEVERAGES) + 1))

    def test_label_carries_the_leverage_as_a_whole_number(self):
        self.assertEqual(label('LEAPS_85_30_TREASURY', 1.25), 'LEAPS_85_30_TSY125')
        self.assertEqual(label('LEAPS_85_30_TREASURY', 1.), 'LEAPS_85_30_TSY100')


class RollingCompoundTests(unittest.TestCase):
    def test_matches_the_naive_computation(self):
        rng = np.random.default_rng(4)
        returns = rng.normal(.0004, .012, 500)
        rolled = rolling_compound(returns, 63)
        self.assertEqual(len(rolled), len(returns) - 63 + 1)
        for position in (0, 17, len(rolled) - 1):
            expected = np.prod(1 + returns[position:position + 63]) - 1
            self.assertAlmostEqual(rolled[position], expected, 12)

    def test_a_total_loss_compounds_to_minus_one_without_poisoning_later_windows(self):
        returns = np.full(20, .01)
        returns[5] = -1.
        rolled = rolling_compound(returns, 3)
        self.assertAlmostEqual(rolled[3], -1., 9)
        self.assertTrue(np.isfinite(rolled).all())
        self.assertGreater(rolled[-1], 0)

    def test_refuses_a_window_longer_than_the_series(self):
        with self.assertRaises(ValueError):
            rolling_compound(np.zeros(10), 11)


class JointLossTests(unittest.TestCase):
    def setUp(self):
        self.dates = pd.bdate_range('2000-01-03', periods=800)

    def test_counts_only_windows_where_both_sleeves_fall(self):
        """One sleeve falling hard is not a joint loss, however bad it is."""
        window, threshold = JOINT_WINDOWS['3m']
        leg = np.full(len(self.dates), -.01)
        sleeve = np.full(len(self.dates), .0005)
        row = joint_loss(leg, sleeve, leg * .5, self.dates)
        self.assertEqual(row['joint_loss_3m'], 0.)
        self.assertTrue(np.isnan(row['worst_joint_12m']))
        self.assertEqual(row['worst_joint_end'], '')

    def test_finds_a_joint_fall(self):
        leg = np.full(len(self.dates), -.01)
        sleeve = np.full(len(self.dates), -.01)
        row = joint_loss(leg, sleeve, leg, self.dates)
        self.assertEqual(row['joint_loss_3m'], 1.)
        self.assertEqual(row['joint_loss_12m'], 1.)
        self.assertLess(row['worst_joint_12m'], -.2)
        self.assertNotEqual(row['worst_joint_end'], '')

    def test_the_quarterly_threshold_is_the_steeper_one(self):
        """-10% in three months is a faster fall than -20% in twelve.

        So a drift mild enough to clear the quarterly test can still trip the
        annual one, and never the other way round.
        """
        leg = sleeve = np.full(len(self.dates), -.001)
        row = joint_loss(leg, sleeve, leg, self.dates)
        self.assertEqual(row['joint_loss_3m'], 0.)
        self.assertEqual(row['joint_loss_12m'], 1.)


class SleeveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.financing = bond_financing(cls.inputs)
        cls.treasury = cls.inputs.daily.loc[cls.inputs.ix, TREASURY]

    def test_fast_sleeve_matches_the_canonical_one(self):
        for leverage in LEVERAGES + (STRESS_LEVERAGE,):
            canonical = constant_leverage(self.treasury, self.financing, leverage, 'D')
            fast = daily_reset(self.treasury.to_numpy(), self.financing.to_numpy(), leverage)
            np.testing.assert_allclose(np.cumprod(1 + fast),
                                       np.cumprod(1 + canonical.to_numpy()),
                                       rtol=1e-10, err_msg=f'{leverage}x')

    def test_unlevered_sleeve_is_the_series_itself(self):
        np.testing.assert_array_equal(
            daily_reset(self.treasury.to_numpy(), self.financing.to_numpy(), 1.),
            self.treasury.to_numpy())
        # The canonical sleeve compounds a wealth path and differences it, so it
        # agrees to round-off rather than bit for bit.
        np.testing.assert_allclose(
            treasury_sleeve(self.inputs, 1., self.financing).to_numpy(),
            self.treasury.to_numpy(), atol=1e-15)

    def test_leverage_scales_the_sleeve_and_charges_for_it(self):
        base = daily_reset(self.treasury.to_numpy(), self.financing.to_numpy(), 1.)
        levered = daily_reset(self.treasury.to_numpy(), self.financing.to_numpy(), 2.)
        # Twice the exposure, less one financed dollar.
        np.testing.assert_allclose(levered, 2 * base - self.financing.to_numpy(), atol=1e-15)

    def test_wipeout_is_absorbing(self):
        underlying = np.array([.0, -.6, .5, .5])
        financing = np.zeros(4)
        out = daily_reset(underlying, financing, 2.)
        self.assertAlmostEqual(out[1], -1., 12)
        self.assertEqual(out[2], 0.)
        self.assertEqual(out[3], 0.)

    def test_short_leverage_is_refused(self):
        with self.assertRaises(ValueError):
            daily_reset(np.zeros(5), np.zeros(5), .5)


class HistoricalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.frame = historical_frontier(cls.inputs)

    def test_every_row_is_a_distinct_strategy(self):
        self.assertEqual(len(self.frame), len(self.frame.strategy.unique()))
        self.assertEqual(len(self.frame),
                         len(STRUCTURES) * (len(LEVERAGES) + 2))

    def test_gross_notional_is_the_two_sleeves(self):
        np.testing.assert_allclose(
            self.frame.gross_notional,
            self.frame.mean_delta_exposure + self.frame.treasury_notional, atol=1e-12)

    def test_treasury_notional_tracks_weight_times_leverage(self):
        """The sleeve holds the residual after premium, levered."""
        for structure, (_, budget) in STRUCTURES.items():
            for leverage in LEVERAGES:
                row = self.frame.set_index('strategy').loc[label(structure, leverage)]
                self.assertAlmostEqual(row.treasury_notional, (1 - budget) * leverage,
                                       delta=.12 * leverage)

    def test_unlevered_rows_pay_no_financing(self):
        unlevered = self.frame[self.frame.treasury_leverage == 1.]
        np.testing.assert_allclose(unlevered.financing_drag_bps, 0., atol=1e-9)

    def test_financing_drag_rises_with_leverage(self):
        for structure in STRUCTURES:
            drags = [self.frame.set_index('strategy').loc[label(structure, leverage),
                                                          'financing_drag_bps']
                     for leverage in LEVERAGES]
            self.assertEqual(drags, sorted(drags), structure)

    def test_the_fund_costs_more_than_the_financing_it_replaces(self):
        indexed = self.frame.set_index('strategy')
        for structure in STRUCTURES:
            fund = indexed.loc[f'{short(structure)}_TMF']
            built = indexed.loc[label(structure, STRESS_LEVERAGE)]
            self.assertGreater(fund.financing_drag_bps, built.financing_drag_bps)
            self.assertLess(fund.cagr, built.cagr)
            # Same exposure, different instrument.
            self.assertAlmostEqual(fund.treasury_notional, built.treasury_notional, delta=.02)

    def test_joint_losses_become_more_common_with_leverage(self):
        indexed = self.frame.set_index('strategy')
        for structure in STRUCTURES:
            shares = [indexed.loc[label(structure, leverage), 'joint_loss_3m']
                      for leverage in LEVERAGES]
            self.assertEqual(shares, sorted(shares), structure)


class ShockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.pool = treasury_pool(cls.inputs)

    def test_pool_carries_the_financing_column(self):
        self.assertEqual(len(self.pool), len(self.inputs.ix))
        np.testing.assert_allclose(self.pool[:, -1], bond_financing(self.inputs).to_numpy())
        self.assertTrue(np.isfinite(self.pool).all())

    def test_sample_is_warm_up_plus_the_replayed_episode(self):
        for repeats in SHOCK_REPEATS:
            sample, stressed = shock_sample(self.inputs, self.pool, repeats)
            self.assertEqual(len(sample), WARMUP_SESSIONS + stressed)
            self.assertEqual(stressed % repeats, 0)

    def test_one_repeat_reproduces_the_realized_episode(self):
        """The construction has to give back 2022 before it is trusted to extend it."""
        start, end = CRASHES['2022_rates']
        realized = self.inputs.daily.loc[self.inputs.ix, TREASURY].loc[start:end]
        sample, _ = shock_sample(self.inputs, self.pool, 1)
        replayed = sample[WARMUP_SESSIONS:, 2]
        np.testing.assert_allclose(replayed, realized.to_numpy())

    def test_more_leverage_is_worse_through_the_shock(self):
        frame = shock_table(self.inputs, self.pool, leverages=LEVERAGES)
        for (structure, repeats), group in frame.groupby(['structure', 'repeats']):
            ordered = group.sort_values('treasury_leverage').mean_return.to_numpy()
            self.assertTrue(np.all(np.diff(ordered) < 0), (structure, repeats))

    def test_a_longer_shock_is_worse_than_a_shorter_one(self):
        frame = shock_table(self.inputs, self.pool, leverages=(1., 2.))
        for (structure, leverage), group in frame.groupby(['structure', 'treasury_leverage']):
            ordered = group.sort_values('repeats').mean_return.to_numpy()
            self.assertTrue(np.all(np.diff(ordered) < 0), (structure, leverage))

    def test_roll_phase_moves_the_answer_less_than_leverage_does(self):
        frame = shock_table(self.inputs, self.pool, leverages=LEVERAGES).set_index('strategy')
        structure = list(STRUCTURES)[1]
        worst = frame[frame.repeats == max(SHOCK_REPEATS)]
        spread = float(worst.loc[[label(structure, v) for v in LEVERAGES],
                                 'roll_luck_spread'].max())
        span = float(worst.loc[label(structure, 1.), 'mean_return']
                     - worst.loc[label(structure, LEVERAGES[-1]), 'mean_return'])
        self.assertLess(spread, span / 3)

    def test_the_calendar_moves_but_the_data_does_not(self):
        sample, _ = shock_sample(self.inputs, self.pool, 1)
        structure = list(STRUCTURES)[1]
        outcomes = {offset: shock_path(self.inputs, sample, structure, 1., offset).navs[-1]
                    for offset in SHOCK_OFFSETS}
        self.assertEqual(len(set(outcomes)), len(SHOCK_OFFSETS))
        self.assertLess(max(outcomes.values()) - min(outcomes.values()), .05)


class SimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.pool = treasury_pool(cls.inputs)
        cls.horizon = build_horizon(cls.inputs, 20, (ROLL_LABEL,))

    def test_every_leverage_is_scored_on_every_path(self):
        store = monte_carlo(self.pool, self.horizon, LEVERAGES, 4, PRIMARY_BLOCK, SEED,
                            workers=1, runner=run_path)
        self.assertEqual(len(store), 1 + len(STRUCTURES) * len(LEVERAGES))
        for structure in STRUCTURES:
            for leverage in LEVERAGES:
                self.assertIn(label(structure, leverage), store)

    def test_exposures_are_the_two_sleeves(self):
        store = monte_carlo(self.pool, self.horizon, LEVERAGES, 4, PRIMARY_BLOCK, SEED,
                            workers=1, runner=run_path)
        for name, values in store.items():
            if name == 'SP500_1X':
                self.assertTrue(np.isnan(values[:, SLOT['treasury_notional']]).all())
                continue
            np.testing.assert_allclose(values[:, SLOT['gross_notional']],
                                       values[:, SLOT['mean_delta_exposure']]
                                       + values[:, SLOT['treasury_notional']], atol=1e-12)

    def test_parallel_and_serial_agree_exactly(self):
        serial = monte_carlo(self.pool, self.horizon, LEVERAGES, 8, PRIMARY_BLOCK, SEED,
                             workers=1, runner=run_path)
        parallel = monte_carlo(self.pool, self.horizon, LEVERAGES, 8, PRIMARY_BLOCK, SEED,
                               workers=3, runner=run_path)
        for name, values in serial.items():
            np.testing.assert_array_equal(values, parallel[name], err_msg=name)

    def test_nothing_trails_itself(self):
        store = monte_carlo(self.pool, self.horizon, LEVERAGES, 6, PRIMARY_BLOCK, SEED,
                            workers=1, runner=run_path)
        frame = summarize(store, self.horizon, PRIMARY_BLOCK, 6).set_index('strategy')
        self.assertEqual(frame.loc['SP500_1X', 'prob_below_SP500_1X'], 0.)
        for reference in ('LEAPS_85_30_TSY100', 'LEAPS_95_50_TSY100'):
            self.assertEqual(frame.loc[reference, f'prob_below_{reference}'], 0.)

    def test_probabilities_and_percentiles_are_well_formed(self):
        store = monte_carlo(self.pool, self.horizon, LEVERAGES, 12, PRIMARY_BLOCK, SEED,
                            workers=1, runner=run_path)
        frame = summarize(store, self.horizon, PRIMARY_BLOCK, 12)
        columns = [c for c in frame.columns if c.startswith('prob_')]
        values = frame[columns].to_numpy(dtype=float)
        self.assertTrue(np.all(((values >= 0) & (values <= 1)) | np.isnan(values)))
        for _, row in frame.iterrows():
            self.assertLessEqual(row.cagr_p5, row.cagr_p50)
            self.assertLessEqual(row.cagr_p50, row.cagr_p95)
            self.assertLessEqual(row.max_drawdown_p95, row.max_drawdown_p90)
            self.assertLessEqual(row.max_drawdown_p90, row.median_max_drawdown)

    def test_deeper_drawdowns_are_never_more_likely(self):
        store = monte_carlo(self.pool, self.horizon, LEVERAGES, 12, PRIMARY_BLOCK, SEED,
                            workers=1, runner=run_path)
        frame = summarize(store, self.horizon, PRIMARY_BLOCK, 12)
        thresholds = [40, 50, 60, 75]
        for _, row in frame.iterrows():
            probabilities = [row[f'prob_drawdown_worse_than_{t}'] for t in thresholds]
            self.assertEqual(probabilities, sorted(probabilities, reverse=True))


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        names = [label(s, v) for s in STRUCTURES for v in LEVERAGES]
        rng = np.random.default_rng(2)
        self.thirty = pd.DataFrame(
            dict(cagr_p50=rng.uniform(.10, .18, len(names)),
                 cagr_p5=rng.uniform(.03, .08, len(names)),
                 median_max_drawdown=-rng.uniform(.3, .7, len(names)),
                 prob_drawdown_worse_than_60=rng.uniform(0, .8, len(names))),
            index=names)

    def test_price_is_drawdown_over_return(self):
        frame = exchange_rates(self.thirty)
        finite = frame[np.isfinite(frame.drawdown_per_cagr_point)]
        np.testing.assert_allclose(finite.drawdown_per_cagr_point,
                                   finite.drawdown_given_up / finite.cagr_gained, rtol=1e-12)

    def test_covers_both_routes(self):
        frame = exchange_rates(self.thirty)
        self.assertEqual(set(frame.route), {'sleeve leverage', 'premium budget'})
        self.assertEqual((frame.route == 'sleeve leverage').sum(),
                         len(STRUCTURES) * (len(LEVERAGES) - 1))
        self.assertEqual((frame.route == 'premium budget').sum(), len(STRUCTURES) - 1)


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.pool = treasury_pool(cls.inputs)
        cls.historical = historical_frontier(cls.inputs)
        cls.shock = shock_table(cls.inputs, cls.pool, leverages=LEVERAGES + (STRESS_LEVERAGE,))
        frames = []
        for years in (20, 30):
            horizon = build_horizon(cls.inputs, years, (ROLL_LABEL,))
            store = monte_carlo(cls.pool, horizon, LEVERAGES, 6, PRIMARY_BLOCK, SEED,
                                workers=1, runner=run_path)
            frames.append(summarize(store, horizon, PRIMARY_BLOCK, 6))
        cls.monte = pd.concat(frames, ignore_index=True)
        cls.frontier = frontier_table(cls.inputs, cls.pool, cls.historical, cls.monte,
                                      cls.shock)

    def render(self, directory):
        report(directory, self.inputs, self.historical, self.monte, self.shock,
               self.frontier, 6, 4)
        return (directory / 'leaps_treasury_leverage_results.md').read_text()

    def test_answers_all_eight_questions(self):
        with tempfile.TemporaryDirectory() as directory:
            text = self.render(Path(directory))
        for number in range(1, 9):
            self.assertIn(f'**{number}.', text)

    def test_keeps_the_modelled_not_measured_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            text = self.render(Path(directory))
        self.assertIn('modelled, not measured', text)
        self.assertIn('one long decline in bond yields', text)

    def test_leaves_no_unfilled_placeholder(self):
        with tempfile.TemporaryDirectory() as directory:
            text = self.render(Path(directory))
        for token in ('{', '}'):
            self.assertNotIn(token, text, token)
        for token in (r'\bnan\b', r'\bNone\b', r'\binf\b'):
            self.assertIsNone(re.search(token, text), token)

    def test_frontier_covers_every_simulated_strategy(self):
        simulated = set(self.monte[self.monte.horizon_years == 30].strategy)
        self.assertEqual(set(self.frontier.strategy), simulated)
        self.assertTrue(self.frontier.notna().all().all())

    def test_writes_a_figure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'frontier.png'
            figure(path, self.monte, self.shock)
            self.assertGreater(path.stat().st_size, 20000)
