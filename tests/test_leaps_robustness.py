import re
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from letf.hedge_alternatives import EQUITY, LAG, SMA_DAYS, SWITCH_COST_BPS, UPRO
from letf.leaps_robustness import (BENCHMARK_INTERVAL, BLOCK_LENGTHS, MC_STRATEGIES,
                                   MONTE_CARLO_INTERVALS,
                                   PRIMARY_BLOCK, ROLL_INTERVALS, SEED, SLOT, STRUCTURES,
                                   WARMUP_SESSIONS, Horizon, block_draw, bootstrap_pool,
                                   build_horizon, build_path, drawdown_table, figure,
                                   historical_reference, label_of, load,
                                   model_uncertainty, monte_carlo, path_metrics,
                                   percentile_table, rank_table, rebalanced,
                                   roll_dispersion, roll_frequency_table, run_path,
                                   summarize, report)
from letf.model import portfolio
from letf.signals import level_position
from letf.strategy import select_returns, switching_costs

ROOT = Path(__file__).resolve().parents[1]


class BlockDrawTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(0)

    def test_returns_exactly_the_requested_length(self):
        for needed in (10, 100, 1001):
            self.assertEqual(len(block_draw(self.rng, 500, needed, 63)), needed)

    def test_indices_stay_inside_the_pool(self):
        drawn = block_draw(self.rng, 500, 2000, 63)
        self.assertGreaterEqual(drawn.min(), 0)
        self.assertLess(drawn.max(), 500)

    def test_days_inside_a_block_stay_consecutive(self):
        """The whole point: within a block, history is untouched."""
        block = 21
        drawn = block_draw(self.rng, 500, block * 10, block)
        for start in range(0, len(drawn), block):
            piece = drawn[start:start + block]
            np.testing.assert_array_equal(np.diff(piece), np.ones(len(piece) - 1))

    def test_refuses_a_block_longer_than_the_history(self):
        with self.assertRaises(ValueError):
            block_draw(self.rng, 50, 100, 63)


class MetricTests(unittest.TestCase):
    def setUp(self):
        closes = pd.bdate_range('2000-01-03', periods=252 * 12)
        self.horizon = Horizon(10, closes, closes,
                               (closes - closes[0]).days.to_numpy().astype(float),
                               np.array([0]), {}, {10: (np.array([], dtype=int),
                                                        np.array([], dtype=int),
                                                        np.array([])),
                                                 20: (np.array([], dtype=int),
                                                      np.array([], dtype=int),
                                                      np.array([]))},
                               float((closes[-1] - closes[0]).days))

    def test_recovers_a_known_growth_rate(self):
        wealth = np.exp(np.linspace(0, np.log(4), len(self.horizon.closes)))
        out, _ = path_metrics(wealth, self.horizon)
        years = self.horizon.elapsed_days / 365.25
        self.assertAlmostEqual(out[SLOT['cagr']], 4 ** (1 / years) - 1, 10)
        self.assertAlmostEqual(out[SLOT['terminal_wealth']], 4., 10)

    def test_monotone_path_has_no_drawdown(self):
        wealth = np.exp(np.linspace(0, 1, len(self.horizon.closes)))
        out, drawdown = path_metrics(wealth, self.horizon)
        self.assertAlmostEqual(out[SLOT['max_drawdown']], 0., 12)
        self.assertAlmostEqual(out[SLOT['min_wealth']], 1., 12)
        self.assertTrue((drawdown <= 0).all())

    def test_refuses_a_path_that_reached_zero(self):
        wealth = np.ones(len(self.horizon.closes))
        wealth[100:] = 0.
        with self.assertRaises(ValueError):
            path_metrics(wealth, self.horizon)


class RebalancingTests(unittest.TestCase):
    """The fast spelling must be the same portfolio as the canonical one."""

    def setUp(self):
        index = pd.bdate_range('1995-01-02', periods=252 * 8)
        rng = np.random.default_rng(7)
        self.frame = pd.DataFrame(rng.normal(.0004, .012, (len(index), 2)),
                                  index=index, columns=['A', 'B'])
        codes = index.to_period('Q')
        self.starts = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])

    def test_matches_letf_model_portfolio(self):
        weights = pd.Series({'A': .6, 'B': .4})
        fast = rebalanced(self.frame.to_numpy(), self.starts, weights.to_numpy())
        canonical = portfolio(self.frame, weights, 'quarterly').to_numpy()
        np.testing.assert_allclose(np.cumprod(1 + fast), np.cumprod(1 + canonical),
                                   rtol=1e-11)

    def test_a_single_sleeve_is_that_sleeve(self):
        fast = rebalanced(self.frame.to_numpy(), self.starts, np.array([1., 0.]))
        np.testing.assert_allclose(fast, self.frame.A.to_numpy(), atol=1e-15)


class RealDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.pool = bootstrap_pool(cls.inputs)

    def test_pool_rows_line_up_with_the_comparison_window(self):
        self.assertEqual(len(self.pool), len(self.inputs.ix))
        self.assertTrue(np.isfinite(self.pool).all())

    def test_trend_costing_matches_the_canonical_helpers(self):
        """The loop's np.where spelling of the trend rule is `letf.strategy`'s rule."""
        d = self.inputs.daily.loc[self.inputs.ix]
        position = level_position(self.inputs.price, self.inputs.calendar,
                                  SMA_DAYS, LAG).loc[self.inputs.ix]
        canonical = switching_costs(select_returns(d, position, {1: UPRO, 0: EQUITY}),
                                    position, SWITCH_COST_BPS).to_numpy()
        state = position.to_numpy()
        timed = np.where(state == 1, d[UPRO].to_numpy(), d[EQUITY].to_numpy())
        turnover = np.r_[0., (state[1:] != state[:-1]).astype(float)]
        fast = (1 + timed) * (1 - SWITCH_COST_BPS / 10000 * turnover) - 1
        np.testing.assert_allclose(fast, canonical, atol=1e-15)

    def test_horizon_spans_the_years_it_claims(self):
        for years in (20, 30):
            horizon = build_horizon(self.inputs, years, (BENCHMARK_INTERVAL,))
            self.assertAlmostEqual(horizon.elapsed_days / 365.25, years, delta=.02)
            self.assertEqual(len(horizon.calendar) - len(horizon.closes), WARMUP_SESSIONS)

    def test_refuses_a_horizon_the_history_cannot_cover(self):
        with self.assertRaises(ValueError):
            build_horizon(self.inputs, 60, (BENCHMARK_INTERVAL,))

    def test_signals_are_warm_before_any_position_is_opened(self):
        horizon = build_horizon(self.inputs, 20, (BENCHMARK_INTERVAL,))
        sample = self.pool[block_draw(np.random.default_rng(1), len(self.pool),
                                      len(horizon.calendar) - 1, PRIMARY_BLOCK)]
        _, vol, riskfree, dividend, position = build_path(sample, horizon.calendar)
        for series in (vol, riskfree, dividend):
            self.assertTrue(np.isfinite(series[WARMUP_SESSIONS:]).all())
        self.assertFalse(np.isnan(position.to_numpy()[WARMUP_SESSIONS + 1:]).any())

    def test_shorter_nominal_interval_rolls_more_and_holds_less(self):
        frame = roll_frequency_table(self.inputs).set_index(['structure', 'roll'])
        for name in STRUCTURES:
            ordered = sorted(ROLL_INTERVALS, key=ROLL_INTERVALS.get)
            rolls = [frame.loc[(name, label), 'rolls'] for label in ordered]
            held = [frame.loc[(name, label), 'mean_held_years'] for label in ordered]
            self.assertEqual(rolls, sorted(rolls, reverse=True), name)
            self.assertEqual(held, sorted(held), name)

    def test_realized_maturity_is_not_the_nominal_one(self):
        """The listed-expiry calendar is the point: nine months cannot be bought."""
        frame = roll_frequency_table(self.inputs).set_index(['structure', 'roll'])
        held = frame.loc[(list(STRUCTURES)[0], '9m'), 'mean_held_years']
        self.assertGreater(held, .75)
        self.assertLess(abs(frame.loc[(list(STRUCTURES)[0], '12m'), 'mean_held_years'] - 1),
                        .05)

    def test_dearer_options_earn_less(self):
        frame = model_uncertainty(self.inputs).set_index('structure')
        for name in STRUCTURES:
            self.assertGreater(frame.loc[name, 'cagr_one_point_cheaper'],
                               frame.loc[name, 'cagr_at_base'])
            self.assertGreater(frame.loc[name, 'cagr_at_base'],
                               frame.loc[name, 'cagr_one_point_dearer'])
            self.assertGreater(frame.loc[name, 'cagr_per_volatility_point'], 0)

    def test_dispersion_brackets_every_interval(self):
        rolls = roll_frequency_table(self.inputs)
        spread = roll_dispersion(rolls).set_index('structure')
        for name, group in rolls.groupby('structure'):
            self.assertLessEqual(spread.loc[name, 'cagr_min'], group.cagr.min() + 1e-12)
            self.assertGreaterEqual(spread.loc[name, 'cagr_max'], group.cagr.max() - 1e-12)


class SimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.pool = bootstrap_pool(cls.inputs)
        cls.horizon = build_horizon(cls.inputs, 20, MONTE_CARLO_INTERVALS)

    def draw(self, seed=3):
        return self.pool[block_draw(np.random.default_rng(seed), len(self.pool),
                                    len(self.horizon.calendar) - 1, PRIMARY_BLOCK)]

    def test_every_strategy_is_scored_on_every_path(self):
        result = run_path(self.draw(), self.horizon, MONTE_CARLO_INTERVALS)
        self.assertEqual(len(result), 3 + len(STRUCTURES) * len(MONTE_CARLO_INTERVALS))
        for name in MC_STRATEGIES:
            self.assertIn(name, result)

    def test_benchmark_roll_carries_the_bare_name(self):
        self.assertEqual(label_of('X', BENCHMARK_INTERVAL), 'X')
        self.assertNotEqual(label_of('X', '18m'), 'X')

    def test_option_diagnostics_are_only_scored_for_options(self):
        result = run_path(self.draw(), self.horizon, MONTE_CARLO_INTERVALS)
        self.assertTrue(np.isnan(result['SP500_1X'][SLOT['mean_delta_exposure']]))
        for name in STRUCTURES:
            self.assertGreater(result[name][SLOT['mean_delta_exposure']], 0)
            self.assertGreater(result[name][SLOT['rolls']], 0)

    def test_parallel_and_serial_agree_exactly(self):
        serial = monte_carlo(self.pool, self.horizon, MONTE_CARLO_INTERVALS, 8,
                             PRIMARY_BLOCK, SEED, workers=1)
        parallel = monte_carlo(self.pool, self.horizon, MONTE_CARLO_INTERVALS, 8,
                               PRIMARY_BLOCK, SEED, workers=3)
        for name, values in serial.items():
            np.testing.assert_array_equal(values, parallel[name], err_msg=name)

    def test_the_same_seed_gives_the_same_answer(self):
        first = monte_carlo(self.pool, self.horizon, (BENCHMARK_INTERVAL,), 6,
                            PRIMARY_BLOCK, SEED, workers=1)
        second = monte_carlo(self.pool, self.horizon, (BENCHMARK_INTERVAL,), 6,
                             PRIMARY_BLOCK, SEED, workers=1)
        for name, values in first.items():
            np.testing.assert_array_equal(values, second[name])

    def test_a_different_seed_gives_a_different_answer(self):
        first = monte_carlo(self.pool, self.horizon, (BENCHMARK_INTERVAL,), 6,
                            PRIMARY_BLOCK, SEED, workers=1)
        other = monte_carlo(self.pool, self.horizon, (BENCHMARK_INTERVAL,), 6,
                            PRIMARY_BLOCK, SEED + 1, workers=1)
        self.assertFalse(np.array_equal(first['SP500_1X'], other['SP500_1X']))

    def test_a_prefix_of_the_paths_is_the_same_run(self):
        """Path outcomes depend on the path index alone, not on how many were run."""
        few = monte_carlo(self.pool, self.horizon, (BENCHMARK_INTERVAL,), 4,
                          PRIMARY_BLOCK, SEED, workers=1)
        many = monte_carlo(self.pool, self.horizon, (BENCHMARK_INTERVAL,), 9,
                           PRIMARY_BLOCK, SEED, workers=1)
        for name, values in few.items():
            np.testing.assert_array_equal(values, many[name][:4])

    def test_the_index_never_beats_itself(self):
        store = monte_carlo(self.pool, self.horizon, (BENCHMARK_INTERVAL,), 8,
                            PRIMARY_BLOCK, SEED, workers=1)
        frame = summarize(store, self.horizon, PRIMARY_BLOCK, 8).set_index('strategy')
        self.assertEqual(frame.loc['SP500_1X', 'prob_below_sp500'], 0.)

    def test_probabilities_stay_in_range(self):
        store = monte_carlo(self.pool, self.horizon, (BENCHMARK_INTERVAL,), 8,
                            PRIMARY_BLOCK, SEED, workers=1)
        frame = summarize(store, self.horizon, PRIMARY_BLOCK, 8)
        columns = [c for c in frame.columns if c.startswith('prob_')]
        values = frame[columns].to_numpy(dtype=float)
        self.assertTrue(np.all((values >= 0) & (values <= 1) | np.isnan(values)))

    def test_deeper_drawdowns_are_never_more_likely(self):
        store = monte_carlo(self.pool, self.horizon, (BENCHMARK_INTERVAL,), 12,
                            PRIMARY_BLOCK, SEED, workers=1)
        frame = drawdown_table(store, self.horizon, PRIMARY_BLOCK)
        for _, group in frame.groupby('strategy'):
            ordered = group.sort_values('threshold').probability.to_numpy()
            self.assertTrue(np.all(np.diff(ordered) <= 1e-12))

    def test_percentiles_are_ordered(self):
        store = monte_carlo(self.pool, self.horizon, (BENCHMARK_INTERVAL,), 12,
                            PRIMARY_BLOCK, SEED, workers=1)
        frame = percentile_table(store, self.horizon, PRIMARY_BLOCK)
        for _, group in frame.groupby(['strategy', 'metric']):
            ordered = group.set_index('statistic').loc[['p5', 'p10', 'p50', 'p90', 'p95'],
                                                       'value'].to_numpy()
            self.assertTrue(np.all(np.diff(ordered) >= -1e-12))


class ReportTests(unittest.TestCase):
    """One end-to-end pass: the report must render every claim it makes."""

    @classmethod
    def setUpClass(cls):
        inputs = load(ROOT)
        pool = bootstrap_pool(inputs)
        cls.rolls = roll_frequency_table(inputs)
        cls.dispersion = roll_dispersion(cls.rolls)
        cls.uncertainty = model_uncertainty(inputs)
        cls.reference = historical_reference(inputs, cls.rolls)
        cls.summaries, ranks, blocks, cls.stores = {}, [], [], {}
        for years in (20, 30):
            horizon = build_horizon(inputs, years, MONTE_CARLO_INTERVALS)
            store = monte_carlo(pool, horizon, MONTE_CARLO_INTERVALS, 6, PRIMARY_BLOCK,
                                SEED, workers=1)
            cls.stores[years] = (horizon, store)
            cls.summaries[years] = summarize(store, horizon, PRIMARY_BLOCK, 6)
            ranks.append(rank_table(store, cls.reference, horizon, PRIMARY_BLOCK))
            # Mirror run(): every prespecified block length appears, the primary
            # one reusing the main store rather than resampling again.
            for block in BLOCK_LENGTHS:
                frame = (cls.summaries[years] if block == PRIMARY_BLOCK else
                         summarize(monte_carlo(pool, horizon, (BENCHMARK_INTERVAL,), 4,
                                               block, SEED, workers=1), horizon, block, 4))
                blocks.append(frame[frame.strategy.isin(MC_STRATEGIES)])
        cls.ranks = pd.concat(ranks, ignore_index=True)
        cls.blocks = pd.concat(blocks, ignore_index=True)
        cls.inputs = inputs

    def render(self, directory):
        report(directory, self.inputs, self.rolls, self.dispersion, self.uncertainty,
               self.reference, self.summaries, self.ranks, self.blocks, self.stores, 6, 4)
        return (directory / 'leaps_roll_monte_carlo_results.md').read_text()

    def test_answers_all_seven_questions(self):
        with tempfile.TemporaryDirectory() as directory:
            text = self.render(Path(directory))
        for number in range(1, 8):
            self.assertIn(f'**{number}.', text)

    def test_keeps_the_modelled_not_measured_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            text = self.render(Path(directory))
        self.assertIn('modelled, not measured', text)
        self.assertIn('no number in it is a forecast', text)

    def test_leaves_no_unfilled_placeholder(self):
        """A missing value must break the report, not print as a bare word."""
        with tempfile.TemporaryDirectory() as directory:
            text = self.render(Path(directory))
        for token in ('{', '}'):
            self.assertNotIn(token, text, token)
        # Word boundaries: "financing" is not a stray NaN.
        for token in (r'\bnan\b', r'\bNone\b', r'\binf\b'):
            self.assertIsNone(re.search(token, text), token)

    def test_ranks_cover_every_simulated_strategy(self):
        listed = set(self.ranks[self.ranks.horizon_years == 30].strategy)
        self.assertEqual(listed, set(MC_STRATEGIES))
        self.assertEqual(sorted(self.reference.historical_rank),
                         list(range(1, len(MC_STRATEGIES) + 1)))

    def test_writes_a_figure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'figure.png'
            figure(path, self.rolls, self.stores[30][1], self.stores[30][0])
            self.assertGreater(path.stat().st_size, 20000)
