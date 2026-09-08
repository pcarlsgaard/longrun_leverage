import ast
import re
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from letf.hedge_alternatives import EQUITY, IV_PREMIUM
from letf.leaps_robustness import (PRIMARY_BLOCK, SEED, SLOT as BASE_SLOT, block_draw,
                                   build_horizon, build_path, monte_carlo)
from letf.nasdaq_leaps import NASDAQ, underlying_inputs
from letf.treasury_leverage import FINANCING, treasury_pool
from letf.leaps_frontier import (ANCHOR, APPROACH_BAND, Arm, BASELINE,
                                 CAGR_TOLERANCE, CASES, COMMITTED, DRAWDOWN_TOLERANCE,
                                 FAMILY, FRONTIER_HORIZON, IV_PREMIUMS, LEVERAGES,
                                 MC_HORIZONS, MC_PATHS, MENU_SIZE, METRICS, NDX_BUDGETS,
                                 NDX_MONEYNESS, NDX_PRICE, NDX_STRUCTURES, NDX_TOTAL,
                                 NDX_YIELD, POOL_WIDTH, PROBABILITY_TOLERANCE,
                                 REGIME_AXES, RETURN_TIERS, ROBUST_HOLDS, ROLL_LABEL,
                                 ROLL_LABELS, SENSITIVITY_ARMS, SLOT, SP_BUDGETS,
                                 SP_MONEYNESS, SP_STRUCTURES, SP_TARGETS, STRUCTURES,
                                 UNDERLYING, Case, _dominates, _formats,
                                 cheapest_reaching, classify, committed_check, dominators,
                                 frontier_flags, frontier_pool, historical_table, key,
                                 load, ndx_hypotheses, neighbour_support, regimes, report,
                                 resolution, run_path, sp_hypotheses, summarize, survival,
                                 tier_table, volatility_movement)

ROOT = Path(__file__).resolve().parents[1]
PATHS = 8


class LatticeTests(unittest.TestCase):
    def test_the_lattice_is_the_prespecified_one(self):
        self.assertEqual(SP_MONEYNESS, (.85, .90, .95))
        self.assertEqual(SP_BUDGETS, (.30, .35, .40, .45, .50))
        self.assertEqual(NDX_MONEYNESS, (.80, .85, .90, .95))
        self.assertEqual(NDX_BUDGETS, (.20, .25, .30, .35, .40, .45, .50))
        self.assertEqual(len(SP_STRUCTURES), len(SP_MONEYNESS) * len(SP_BUDGETS) + 1)
        self.assertEqual(len(NDX_STRUCTURES), len(NDX_MONEYNESS) * len(NDX_BUDGETS))
        self.assertEqual(len(STRUCTURES), 44)

    def test_no_intermediate_cell_was_added(self):
        """The anchor is the one off-grid cell, and it is named as such."""
        for name, (moneyness, budget) in STRUCTURES.items():
            if name == ANCHOR[0]:
                continue
            grid = ((SP_MONEYNESS, SP_BUDGETS) if UNDERLYING[name] == EQUITY
                    else (NDX_MONEYNESS, NDX_BUDGETS))
            self.assertIn(moneyness, grid[0], name)
            self.assertIn(budget, grid[1], name)
        self.assertEqual(STRUCTURES[ANCHOR[0]], ANCHOR[1])

    def test_names_encode_their_own_parameters(self):
        for name, (moneyness, budget) in STRUCTURES.items():
            prefix, strike, spend = name.split('_')
            self.assertEqual(int(strike), round(moneyness * 100), name)
            self.assertEqual(int(spend), round(budget * 100), name)
            self.assertEqual(prefix, 'SPX' if UNDERLYING[name] == EQUITY else 'NDX')

    def test_each_cell_is_mapped_to_one_underlying_and_family(self):
        self.assertEqual(set(UNDERLYING), set(STRUCTURES))
        self.assertEqual(set(FAMILY), set(STRUCTURES))
        self.assertEqual({UNDERLYING[n] for n in SP_STRUCTURES}, {EQUITY})
        self.assertEqual({UNDERLYING[n] for n in NDX_STRUCTURES}, {NASDAQ})

    def test_the_cells_this_repository_already_published_are_all_in_the_lattice(self):
        for name in COMMITTED:
            self.assertIn(name, STRUCTURES)

    def test_the_specific_hypotheses_name_cells_that_exist(self):
        for name in SP_TARGETS + ('SPX_85_30', 'SPX_85_40', 'SPX_90_35', 'SPX_90_40',
                                  'SPX_90_45', 'SPX_95_40'):
            self.assertIn(name, SP_STRUCTURES, name)


class SpecificationTests(unittest.TestCase):
    def test_path_counts_meet_the_brief(self):
        self.assertGreaterEqual(MC_PATHS, 10000)
        self.assertLessEqual(MC_PATHS, 15000)
        for case in CASES:
            self.assertGreaterEqual(case.paths, 2000, case.name)
            self.assertLessEqual(case.paths, 5000, case.name)

    def test_seven_sensitivities_cover_every_dial(self):
        self.assertEqual(len(SENSITIVITY_ARMS), 7)
        dials = {case.dial for case in CASES}
        self.assertEqual(dials, {'iv_premium', 'block', 'treasury_leverage', 'roll'})
        for case, arm in SENSITIVITY_ARMS:
            match = [c for c in CASES if c.name == case]
            self.assertTrue(match, case)
            self.assertIn(arm, [a.name for a in match[0].arms])

    def test_every_sensitivity_differs_from_the_primary_specification(self):
        """A dial compared against itself would always hold."""
        primary = (PRIMARY_BLOCK, IV_PREMIUM, ROLL_LABEL, 1.)
        for case, arm in SENSITIVITY_ARMS:
            spec = [c for c in CASES if c.name == case][0]
            setting = [a for a in spec.arms if a.name == arm][0]
            self.assertNotEqual(
                (spec.block, setting.iv_premium, setting.roll, setting.leverage),
                primary, f'{case}:{arm}')

    def test_the_dials_are_only_the_prespecified_values(self):
        self.assertEqual(IV_PREMIUMS, (.00, IV_PREMIUM, .06))
        self.assertEqual(LEVERAGES, (1., 1.25))
        self.assertEqual(ROLL_LABELS, ('9m', '12m', '18m'))
        self.assertEqual({case.block for case in CASES}, {21, 63, 126})

    def test_tolerances_and_horizons_are_prespecified(self):
        self.assertEqual((CAGR_TOLERANCE, DRAWDOWN_TOLERANCE, PROBABILITY_TOLERANCE),
                         (.0025, .02, .02))
        self.assertEqual(MC_HORIZONS, (10, 20, 30))
        self.assertEqual(FRONTIER_HORIZON, 30)
        self.assertEqual(RETURN_TIERS, (.13, .15, .17, .19, .21))
        self.assertGreater(ROBUST_HOLDS, len(SENSITIVITY_ARMS) / 2)

    def test_a_block_case_carries_a_matched_baseline(self):
        """So a block result is never compared against a different path count."""
        matched = [c for c in CASES if c.name == 'block_63_matched'][0]
        others = [c for c in CASES if c.dial == 'block' and c is not matched]
        self.assertEqual(matched.block, PRIMARY_BLOCK)
        for case in others:
            self.assertEqual(case.paths, matched.paths, case.name)


class PoolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.market, _ = underlying_inputs(cls.inputs, ROOT)
        cls.pool = frontier_pool(cls.inputs, cls.market)

    def test_the_pool_extends_the_treasury_pool_without_editing_it(self):
        base = treasury_pool(self.inputs)
        self.assertEqual(self.pool.shape[1], POOL_WIDTH)
        self.assertEqual(POOL_WIDTH, base.shape[1] + 3)
        np.testing.assert_array_equal(self.pool[:, :base.shape[1]], base)

    def test_the_nasdaq_columns_are_the_nasdaq_series(self):
        spot = self.market[NASDAQ][0]
        np.testing.assert_allclose(self.pool[:, NDX_PRICE],
                                   spot.pct_change().dropna().to_numpy())
        np.testing.assert_allclose(
            self.pool[:, NDX_TOTAL],
            self.inputs.daily.loc[self.inputs.ix, NASDAQ].to_numpy())
        np.testing.assert_allclose(
            self.pool[:, NDX_YIELD],
            self.market[NASDAQ][1].loc[self.inputs.ix].to_numpy())

    def test_the_financing_column_survives_composition(self):
        self.assertLess(FINANCING, NDX_PRICE)
        self.assertTrue(np.isfinite(self.pool[:, FINANCING]).all())

    def test_no_row_terminates_a_position(self):
        self.assertTrue((self.pool[:, :FINANCING] > -1).all())

    def test_a_shared_seed_selects_the_same_days_at_a_shared_block(self):
        needed = 500
        first = block_draw(np.random.default_rng(np.random.SeedSequence(SEED).spawn(1)[0]),
                           len(self.pool), needed, PRIMARY_BLOCK)
        second = block_draw(np.random.default_rng(np.random.SeedSequence(SEED).spawn(1)[0]),
                            len(self.pool), needed, PRIMARY_BLOCK)
        np.testing.assert_array_equal(first, second)

    def test_a_smaller_run_sees_a_prefix_of_a_larger_run_s_worlds(self):
        """Nested worlds are what let a 2,000-path arm be read beside a 3,000-path one."""
        many = np.random.SeedSequence(SEED).spawn(6)
        few = np.random.SeedSequence(SEED).spawn(3)
        for a, b in zip(many, few):
            self.assertEqual(a.spawn_key, b.spawn_key)


class PathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.market, _ = underlying_inputs(cls.inputs, ROOT)
        cls.pool = frontier_pool(cls.inputs, cls.market)
        cls.horizon = build_horizon(cls.inputs, 10, ROLL_LABELS, structures=STRUCTURES)
        rng = np.random.default_rng(7)
        cls.sample = cls.pool[block_draw(rng, len(cls.pool),
                                         len(cls.horizon.calendar) - 1, PRIMARY_BLOCK)]
        cls.arms = (BASELINE, Arm('dear', iv_premium=.06), Arm('long', roll='18m'),
                    Arm('levered', leverage=1.25))
        cls.result = run_path(cls.sample, cls.horizon, cls.arms)

    def test_every_arm_scores_every_strategy_and_both_indices(self):
        expected = {key(arm.name, name) for arm in self.arms
                    for name in list(STRUCTURES) + [EQUITY, NASDAQ]}
        self.assertEqual(set(self.result), expected)
        for row in self.result.values():
            self.assertEqual(len(row), len(METRICS))

    def test_the_index_rows_do_not_depend_on_any_dial(self):
        for name in (EQUITY, NASDAQ):
            reference = self.result[key('baseline', name)]
            for arm in self.arms[1:]:
                np.testing.assert_array_equal(self.result[key(arm.name, name)], reference)

    def test_a_dearer_option_lowers_the_option_structures_but_not_the_indices(self):
        for name in STRUCTURES:
            base = self.result[key('baseline', name)][BASE_SLOT['terminal_wealth']]
            dear = self.result[key('dear', name)][BASE_SLOT['terminal_wealth']]
            self.assertLess(dear, base, name)

    def test_a_longer_roll_changes_the_structures(self):
        differing = [n for n in STRUCTURES
                     if self.result[key('long', n)][BASE_SLOT['terminal_wealth']]
                     != self.result[key('baseline', n)][BASE_SLOT['terminal_wealth']]]
        self.assertEqual(len(differing), len(STRUCTURES))

    def test_leverage_raises_the_notional_and_the_gross(self):
        """Not by exactly 1.25x: a differently growing sleeve moves the weights too."""
        for name in STRUCTURES:
            base = self.result[key('baseline', name)]
            levered = self.result[key('levered', name)]
            ratio = (levered[SLOT['treasury_notional']]
                     / base[SLOT['treasury_notional']])
            self.assertGreater(levered[SLOT['gross_notional']],
                               base[SLOT['gross_notional']], name)
            self.assertAlmostEqual(ratio, 1.25, places=1, msg=name)

    def test_the_treasury_notional_is_the_levered_complement_of_the_option_weight(self):
        for arm in self.arms:
            for name in STRUCTURES:
                row = self.result[key(arm.name, name)]
                self.assertAlmostEqual(
                    row[SLOT['treasury_notional']],
                    arm.leverage * (1 - row[SLOT['mean_option_weight']]), places=12)
                self.assertAlmostEqual(
                    row[SLOT['gross_notional']],
                    row[BASE_SLOT['mean_delta_exposure']]
                    + row[SLOT['treasury_notional']], places=12)

    def test_a_bigger_budget_buys_more_delta(self):
        for moneyness in NDX_MONEYNESS:
            deltas = [self.result[key('baseline',
                                      f'NDX_{round(moneyness * 100)}_{round(b * 100)}')][
                          BASE_SLOT['mean_delta_exposure']] for b in NDX_BUDGETS]
            self.assertEqual(deltas, sorted(deltas), moneyness)

    def test_serial_and_parallel_runs_agree(self):
        serial = monte_carlo(self.pool, self.horizon, (BASELINE,), 4, PRIMARY_BLOCK,
                             SEED, workers=1, runner=run_path)
        parallel = monte_carlo(self.pool, self.horizon, (BASELINE,), 4, PRIMARY_BLOCK,
                               SEED, workers=2, runner=run_path)
        self.assertEqual(set(serial), set(parallel))
        for name in serial:
            np.testing.assert_array_equal(serial[name], parallel[name], name)

    def test_the_volatility_premium_is_the_only_thing_the_arm_changes(self):
        """`build_path` must move volatility and leave the rest of the path alone."""
        base = build_path(self.sample, self.horizon.calendar, signal=False)
        dear = build_path(self.sample, self.horizon.calendar, signal=False, iv_premium=.06)
        np.testing.assert_array_equal(base[0], dear[0])
        np.testing.assert_array_equal(base[2], dear[2])
        np.testing.assert_array_equal(base[3], dear[3])
        self.assertTrue((dear[1] > base[1]).all())


class DominationTests(unittest.TestCase):
    def test_a_strictly_better_point_dominates(self):
        self.assertTrue(_dominates(.20, .10, -.30, -.60, CAGR_TOLERANCE,
                                   DRAWDOWN_TOLERANCE))

    def test_points_inside_the_tolerances_do_not_dominate_each_other(self):
        self.assertFalse(_dominates(.1501, .1500, -.400, -.401, CAGR_TOLERANCE,
                                    DRAWDOWN_TOLERANCE))
        self.assertFalse(_dominates(.1500, .1501, -.401, -.400, CAGR_TOLERANCE,
                                    DRAWDOWN_TOLERANCE))

    def test_more_return_at_the_same_risk_dominates(self):
        self.assertTrue(_dominates(.20, .10, -.50, -.50, CAGR_TOLERANCE,
                                   DRAWDOWN_TOLERANCE))

    def test_less_risk_at_the_same_return_dominates(self):
        self.assertTrue(_dominates(.15, .15, -.30, -.50, CAGR_TOLERANCE,
                                   DRAWDOWN_TOLERANCE))


class FrontierTests(unittest.TestCase):
    @staticmethod
    def frame(rows):
        return pd.DataFrame([
            dict(strategy=name, cagr_p50=cagr, cagr_p5=p5, max_drawdown_median=dd,
                 prob_drawdown_worse_than_60=tail)
            for name, cagr, p5, dd, tail in rows])

    def test_the_extremes_are_always_non_dominated(self):
        names = list(STRUCTURES)[:5]
        rows = [(names[i], .10 + .02 * i, .05 + .01 * i, -.30 - .08 * i, .05 * i)
                for i in range(5)]
        flags = frontier_flags(self.frame(rows)).set_index('strategy')
        self.assertTrue(flags.loc[names[0], 'on_frontier'])
        self.assertTrue(flags.loc[names[-1], 'on_frontier'])

    def test_a_point_beaten_on_both_axes_is_dominated(self):
        good, bad = list(STRUCTURES)[0], list(STRUCTURES)[1]
        frame = self.frame([(good, .18, .09, -.30, .02), (bad, .12, .04, -.60, .40)])
        flags = frontier_flags(frame).set_index('strategy')
        self.assertTrue(flags.loc[good, 'on_frontier'])
        self.assertFalse(flags.loc[bad, 'on_frontier'])
        overall, within = dominators(frame)
        self.assertEqual(overall[bad], good)

    def test_the_index_rows_are_excluded_from_the_comparison(self):
        frame = self.frame([(list(STRUCTURES)[0], .18, .09, -.60, .40),
                            (EQUITY, .11, .07, -.20, .00)])
        flags = frontier_flags(frame).set_index('strategy')
        self.assertTrue(flags.loc[list(STRUCTURES)[0], 'on_frontier'])
        self.assertFalse(bool(flags.loc[EQUITY, 'on_frontier']))
        self.assertTrue(pd.isna(flags.loc[EQUITY, 'on_growth_frontier']))
        self.assertTrue(pd.isna(flags.loc[EQUITY, 'on_robust_frontier']))


class NeighbourTests(unittest.TestCase):
    def setUp(self):
        self.classification = pd.DataFrame(
            [dict(strategy=name, classification='robust frontier') for name in STRUCTURES])

    def test_an_interior_cell_has_four_neighbours_and_a_corner_two(self):
        out = neighbour_support(self.classification).set_index('strategy')
        self.assertEqual(out.loc['NDX_85_30', 'lattice_neighbours'], 4)
        self.assertEqual(out.loc['NDX_80_20', 'lattice_neighbours'], 2)
        self.assertEqual(out.loc['SPX_90_40', 'lattice_neighbours'], 4)

    def test_the_off_grid_anchor_has_none(self):
        out = neighbour_support(self.classification).set_index('strategy')
        self.assertTrue(pd.isna(out.loc[ANCHOR[0], 'lattice_neighbours']))

    def test_support_is_one_when_the_whole_lattice_is_efficient(self):
        out = neighbour_support(self.classification)
        self.assertTrue((out.neighbour_support.dropna() == 1).all())


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.historical = pd.DataFrame([
            dict(strategy='SPX_85_30', cagr_per_volatility_point=.0053),
            dict(strategy='SPX_95_50', cagr_per_volatility_point=.0139),
            dict(strategy='NDX_80_20', cagr_per_volatility_point=.0001),
        ]).set_index('strategy')

    def test_the_bar_is_the_more_price_sensitive_of_the_pair(self):
        self.assertAlmostEqual(resolution(self.historical, ('SPX_85_30', 'SPX_95_50')),
                               .0139)

    def test_the_bar_never_falls_below_the_prespecified_tolerance(self):
        self.assertEqual(resolution(self.historical, ('NDX_80_20',)), CAGR_TOLERANCE)


class SummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.market, _ = underlying_inputs(cls.inputs, ROOT)
        cls.pool = frontier_pool(cls.inputs, cls.market)
        cls.horizon = build_horizon(cls.inputs, 10, (ROLL_LABEL,), structures=STRUCTURES)
        cls.case = Case('primary', PRIMARY_BLOCK, PATHS, (BASELINE,), 'none')
        cls.store = monte_carlo(cls.pool, cls.horizon, cls.case.arms, PATHS,
                                PRIMARY_BLOCK, SEED, workers=1, runner=run_path)
        cls.frame = summarize(cls.store, cls.horizon, cls.case, paired=True)

    def test_percentiles_are_ordered(self):
        for _, row in self.frame.iterrows():
            self.assertLessEqual(row.cagr_p5, row.cagr_p10)
            self.assertLessEqual(row.cagr_p10, row.cagr_p50)
            self.assertLessEqual(row.cagr_p50, row.cagr_p90)
            self.assertLessEqual(row.cagr_p90, row.cagr_p95)
            self.assertLessEqual(row.terminal_wealth_p5, row.terminal_wealth_p95)

    def test_drawdown_severity_percentiles_deepen(self):
        for _, row in self.frame.iterrows():
            self.assertGreaterEqual(row.max_drawdown_median, row.max_drawdown_p90)
            self.assertGreaterEqual(row.max_drawdown_p90, row.max_drawdown_p95)
            self.assertGreaterEqual(row.max_drawdown_p95, row.worst_max_drawdown)

    def test_drawdown_probabilities_are_nested(self):
        for _, row in self.frame.iterrows():
            self.assertGreaterEqual(row.prob_drawdown_worse_than_40,
                                    row.prob_drawdown_worse_than_50)
            self.assertGreaterEqual(row.prob_drawdown_worse_than_50,
                                    row.prob_drawdown_worse_than_60)
            self.assertGreaterEqual(row.prob_drawdown_worse_than_60,
                                    row.prob_drawdown_worse_than_75)

    def test_every_probability_is_a_probability(self):
        columns = [c for c in self.frame.columns if c.startswith('prob_')]
        for column in columns:
            values = self.frame[column].dropna()
            self.assertTrue(len(values), column)
            self.assertTrue(values.between(0, 1).all(), column)

    def test_an_index_never_trails_itself(self):
        rows = self.frame.set_index('strategy')
        self.assertEqual(rows.loc[EQUITY, 'prob_below_sp500'], 0.)
        self.assertEqual(rows.loc[NASDAQ, 'prob_below_own_underlying'], 0.)

    def test_relative_probabilities_are_paired_not_marginal(self):
        """Recomputing one by hand from the store must reproduce the summary."""
        rows = self.frame.set_index('strategy')
        name = 'NDX_85_30'
        mine = self.store[key('baseline', name)][:, BASE_SLOT['terminal_wealth']]
        theirs = self.store[key('baseline', NASDAQ)][:, BASE_SLOT['terminal_wealth']]
        self.assertAlmostEqual(rows.loc[name, 'prob_below_own_underlying'],
                               float((mine < theirs).mean()))

    def test_the_adjacent_rival_is_always_less_risky(self):
        rows = self.frame.set_index('strategy')
        named = rows[rows.adjacent_lower_risk.notna()]
        self.assertTrue(len(named))
        for name, row in named.iterrows():
            self.assertGreater(rows.loc[row.adjacent_lower_risk, 'max_drawdown_median'],
                               row.max_drawdown_median, name)
            self.assertTrue(0 <= row.prob_beat_adjacent_lower_risk <= 1)

    def test_the_adjacent_rival_is_on_the_growth_frontier(self):
        rows = self.frame.set_index('strategy')
        frontier = set(rows[rows.on_growth_frontier.fillna(False)].index)
        for rival in rows.adjacent_lower_risk.dropna():
            self.assertIn(rival, frontier)


class HistoricalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.market, _ = underlying_inputs(cls.inputs, ROOT)
        cls.historical = historical_table(cls.inputs, cls.market)

    def test_it_covers_the_lattice_and_both_indices(self):
        self.assertEqual(set(self.historical.strategy),
                         set(STRUCTURES) | {EQUITY, NASDAQ})

    def test_the_published_cells_are_reproduced_not_copied(self):
        check = committed_check(ROOT / 'reports', self.historical)
        self.assertEqual(len(check), len(COMMITTED))
        self.assertLess(check.worst_relative_difference.max(), 1e-8)

    def test_a_disagreeing_cell_is_refused(self):
        broken = self.historical.copy()
        broken.loc[broken.strategy == 'SPX_85_30', 'cagr'] += .01
        with self.assertRaises(ValueError):
            committed_check(ROOT / 'reports', broken)

    def test_the_option_price_unit_grows_with_the_budget(self):
        rows = self.historical.set_index('strategy')
        for moneyness in SP_MONEYNESS:
            units = [rows.loc[f'SPX_{round(moneyness * 100)}_{round(b * 100)}',
                              'cagr_per_volatility_point'] for b in SP_BUDGETS]
            self.assertEqual(units, sorted(units), moneyness)

    def test_a_dearer_option_never_earns_more(self):
        rows = self.historical.set_index('strategy')
        for name in STRUCTURES:
            self.assertGreater(rows.loc[name, 'cagr_one_point_cheaper'],
                               rows.loc[name, 'cagr_one_point_dearer'], name)

    def test_the_sleeves_partition_wealth(self):
        rows = self.historical.set_index('strategy').loc[list(STRUCTURES)]
        np.testing.assert_allclose(rows.mean_option_weight + rows.treasury_notional, 1.)

    def test_the_dot_com_bust_is_present_for_every_cell(self):
        self.assertTrue(self.historical['2000_2002_bust'].notna().all())
        self.assertTrue((self.historical['2000_2002_bust'] < 0).all())

    def test_nasdaq_rows_carry_their_post_proxy_column(self):
        rows = self.historical.set_index('strategy')
        for name in NDX_STRUCTURES:
            self.assertGreater(rows.loc[name, 'cohort_20y_entries_post_proxy'], 0, name)


class ClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.market, _ = underlying_inputs(cls.inputs, ROOT)
        pool = frontier_pool(cls.inputs, cls.market)
        cls.historical = historical_table(cls.inputs, cls.market)
        frames = []
        for years in MC_HORIZONS:
            horizon = build_horizon(cls.inputs, years, (ROLL_LABEL,), structures=STRUCTURES)
            case = Case('primary', PRIMARY_BLOCK, PATHS, (BASELINE,), 'none')
            store = monte_carlo(pool, horizon, case.arms, PATHS, PRIMARY_BLOCK, SEED,
                                workers=1, runner=run_path)
            frames.append(summarize(store, horizon, case, paired=True))
        cls.primary = pd.concat(frames, ignore_index=True)
        horizon = build_horizon(cls.inputs, FRONTIER_HORIZON, ROLL_LABELS,
                                structures=STRUCTURES)
        pieces = []
        for case in CASES:
            store = monte_carlo(pool, horizon, case.arms, PATHS, case.block, SEED,
                                workers=1, runner=run_path)
            pieces.append(summarize(store, horizon, case))
        cls.sensitivities = pd.concat(pieces, ignore_index=True)
        cls.thirty = cls.primary[cls.primary.horizon_years == FRONTIER_HORIZON]
        cls.classification = neighbour_support(classify(cls.thirty, cls.sensitivities))

    def test_every_cell_is_classified_exactly_once(self):
        self.assertEqual(len(self.classification), len(STRUCTURES))
        self.assertEqual(set(self.classification.strategy), set(STRUCTURES))
        self.assertTrue(self.classification.classification.isin(
            ['robust frontier', 'conditional frontier', 'dominated']).all())

    def test_a_dominated_cell_is_never_on_the_primary_frontier(self):
        beaten = self.classification[self.classification.classification == 'dominated']
        self.assertFalse(beaten.on_growth_frontier.any())
        self.assertFalse(beaten.on_robust_frontier.any())

    def test_robust_requires_the_prespecified_majority(self):
        for _, row in self.classification.iterrows():
            if row.classification == 'robust frontier':
                self.assertGreaterEqual(row.sensitivities_held, ROBUST_HOLDS, row.strategy)
            elif row.classification == 'conditional frontier':
                self.assertLess(row.sensitivities_held, ROBUST_HOLDS, row.strategy)

    def test_the_failure_list_complements_the_hold_count(self):
        for _, row in self.classification.iterrows():
            failed = [f for f in row.failed_sensitivities.split(';') if f]
            self.assertEqual(len(failed) + row.sensitivities_held,
                             len(SENSITIVITY_ARMS), row.strategy)

    def test_a_dominated_cell_names_what_beats_it(self):
        beaten = self.classification[self.classification.classification == 'dominated']
        self.assertTrue((beaten.dominated_by != '').all())
        for _, row in beaten.iterrows():
            if row.dominated_by_same_family:
                self.assertEqual(FAMILY[row.dominated_by_same_family], row.family)

    def test_every_sensitivity_arm_produced_rows(self):
        for case, arm in SENSITIVITY_ARMS:
            piece = self.sensitivities[(self.sensitivities.case == case)
                                       & (self.sensitivities.arm == arm)]
            self.assertEqual(len(piece), len(STRUCTURES) + 2, f'{case}:{arm}')

    def test_the_menu_is_small_and_ordered(self):
        chosen = regimes(self.classification, self.thirty.set_index('strategy'),
                         self.historical.set_index('strategy'))
        self.assertLessEqual(len(chosen), MENU_SIZE)
        if len(chosen) > 1:
            self.assertEqual(list(chosen.median_max_drawdown),
                             sorted(chosen.median_max_drawdown, reverse=True))
            self.assertTrue((chosen.sensitivities_held >= ROBUST_HOLDS).all())

    def test_the_menu_only_holds_robust_cells(self):
        chosen = regimes(self.classification, self.thirty.set_index('strategy'),
                         self.historical.set_index('strategy'))
        robust = set(self.classification[
            self.classification.classification == 'robust frontier'].strategy)
        self.assertTrue(set(chosen.strategy) <= robust)

    def test_the_volatility_movement_is_measured_against_its_own_baseline(self):
        movement = volatility_movement(self.sensitivities).set_index('strategy')
        baseline = self.sensitivities[(self.sensitivities.case == 'iv_premium')
                                      & (self.sensitivities.arm == 'iv_3')
                                      ].set_index('strategy')
        for name in STRUCTURES:
            self.assertAlmostEqual(movement.loc[name, 'median_cagr_at_plus_3'],
                                   baseline.loc[name, 'cagr_p50'])

    def test_a_dearer_option_never_helps_and_a_cheaper_one_never_hurts(self):
        movement = volatility_movement(self.sensitivities)
        self.assertTrue((movement.median_cagr_move_when_dearer < 0).all())
        self.assertTrue((movement.median_cagr_move_when_cheaper > 0).all())

    def test_the_tier_table_reports_both_families_at_every_tier(self):
        tiers = tier_table(self.primary, self.historical)
        self.assertEqual(len(tiers), len(RETURN_TIERS) * 2)
        for _, row in tiers.iterrows():
            self.assertIn(row.strategy, STRUCTURES)
            self.assertEqual(FAMILY[row.strategy], row.family)
            if row.within_band:
                self.assertLessEqual(abs(row.distance_from_tier), .005)

    def test_the_nasdaq_hypothesis_covers_nine_candidates_and_three_targets(self):
        frame = ndx_hypotheses(self.primary, self.historical)
        self.assertEqual(len(frame), 27)
        self.assertEqual(set(frame.target), set(SP_TARGETS))
        for _, row in frame.iterrows():
            self.assertLessEqual(row.candidate_budget, .30)
            self.assertLessEqual(STRUCTURES[row.candidate][0], .90)
            self.assertEqual(row.approaches, row.median_30y_cagr_gap >= -APPROACH_BAND)

    def test_the_cheapest_reaching_candidate_is_the_cheapest_one_that_reaches(self):
        frame = ndx_hypotheses(self.primary, self.historical)
        picked = cheapest_reaching(frame)
        self.assertEqual(set(picked), set(SP_TARGETS))
        for target, row in picked.items():
            reaching = frame[(frame.target == target) & frame.approaches]
            if len(reaching):
                self.assertEqual(row.candidate_budget, reaching.candidate_budget.min())

    def test_survival_is_measured_on_every_arm(self):
        frame = ndx_hypotheses(self.primary, self.historical)
        pairs = [(row.candidate, target)
                 for target, row in cheapest_reaching(frame).items()]
        held = survival(self.sensitivities, self.historical, pairs)
        for case, arm in SENSITIVITY_ARMS:
            self.assertIn(f'{case}:{arm}', held.columns)
        self.assertTrue((held.arms_held <= len(SENSITIVITY_ARMS)).all())

    def test_the_step_tests_use_the_option_price_bar(self):
        ladders, steps, pairs = sp_hypotheses(
            self.thirty.set_index('strategy'), self.historical.set_index('strategy'))
        self.assertTrue((steps.resolution_bar >= CAGR_TOLERANCE).all())
        self.assertEqual(len(pairs), 2)
        for _, row in steps.iterrows():
            self.assertEqual(row.economically_resolved,
                             abs(row.median_cagr_gained) > row.resolution_bar)

    def test_the_ladders_walk_every_strike_in_both_families(self):
        ladders, _, _ = sp_hypotheses(self.thirty.set_index('strategy'),
                                      self.historical.set_index('strategy'))
        expected = (len(SP_MONEYNESS) * (len(SP_BUDGETS) - 1)
                    + len(NDX_MONEYNESS) * (len(NDX_BUDGETS) - 1))
        self.assertEqual(len(ladders), expected)


class FormatTests(unittest.TestCase):
    def test_text_booleans_and_counts_are_left_alone(self):
        frame = pd.DataFrame([dict(strategy='SPX_85_30', flag=True, count=3,
                                   rate=.12, wealth=88.5, moneyness=.85)])
        formats = _formats(frame)
        self.assertNotIn('strategy', formats)
        self.assertNotIn('flag', formats)
        self.assertNotIn('count', formats)
        self.assertEqual(formats['rate'], '.2%')
        self.assertEqual(formats['moneyness'], '.2f')

    def test_a_missing_column_is_skipped_rather_than_guessed(self):
        frame = pd.DataFrame([dict(rate=.1)])
        self.assertEqual(_formats(frame, ['rate', 'absent']), {'rate': '.2%'})


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = ClassificationTests
        base.setUpClass()
        cls.base = base
        historical = base.historical
        hist = historical.set_index('strategy')
        thirty = base.thirty.set_index('strategy')
        classification = base.classification.merge(
            volatility_movement(base.sensitivities), on='strategy', how='left')
        ladders, steps, pairs = sp_hypotheses(thirty, hist)
        cls.nasdaq = ndx_hypotheses(base.primary, historical)
        cls.held = survival(base.sensitivities, historical,
                            [(row.candidate, target)
                             for target, row in cheapest_reaching(cls.nasdaq).items()])
        cls.tiers = tier_table(base.primary, historical)
        cls.chosen = regimes(classification, thirty, hist)
        cls.classification = classification
        cls.hypotheses = pd.concat([ladders.assign(question='budget ladder'),
                                    steps.assign(question='S&P steps')], ignore_index=True)
        cls.pairs = pairs
        cls.check = committed_check(ROOT / 'reports', historical)
        cls.historical = historical

    def render(self, directory):
        report(directory, self.base.inputs, self.historical, self.check,
               self.base.primary, self.base.sensitivities, self.classification,
               self.hypotheses, self.pairs, self.nasdaq, self.held, self.tiers,
               self.chosen, PATHS)
        return (directory / 'leaps_frontier_results.md').read_text()

    def text(self):
        with tempfile.TemporaryDirectory() as directory:
            return self.render(Path(directory))

    def test_answers_all_fourteen_questions(self):
        text = self.text()
        for number in range(1, 15):
            self.assertIn(f'**{number}.', text)

    def test_leaves_no_unfilled_placeholder(self):
        text = self.text()
        for token in ('{', '}'):
            self.assertNotIn(token, text, token)
        for token in (r'\bnan\b', r'\bNone\b', r'\binf\b'):
            self.assertIsNone(re.search(token, text), token)

    def test_states_both_uncertainties_before_the_numbers(self):
        text = self.text()
        head = ' '.join(text[:text.index('## Historical record')].split())
        self.assertIn('Model uncertainty', head)
        self.assertIn('Strategy uncertainty', head)
        self.assertIn('skew', head)

    def test_says_the_monte_carlo_rows_were_not_reused(self):
        self.assertIn('deliberately not reused', self.text())

    def test_names_every_lattice_cell(self):
        text = self.text()
        for name in STRUCTURES:
            self.assertIn(name, text, name)

    def test_quotes_the_classification_counts_it_computed(self):
        """Question one reports the family frontier, question six the joint one."""
        text = ' '.join(self.text().split())
        cells = self.classification
        own = cells[(cells.family == 'SP500 LEAPS')
                    & (cells.family_classification == 'robust frontier')]
        joint = cells[(cells.family == 'SP500 LEAPS')
                      & (cells.classification == 'robust frontier')]
        if len(own):
            self.assertIn(f'{len(own)} of the {len(SP_STRUCTURES)} S&P cells', text)
        self.assertIn(f'{len(joint)} of {len(SP_STRUCTURES)} S&P cells', text)
        self.assertEqual(int(cells.classification.value_counts().sum()), len(STRUCTURES))

    def test_records_the_regime_rule_it_used(self):
        text = self.text()
        if len(self.chosen):
            self.assertIn(f'{REGIME_AXES} of the four frontier axes', text)


class FamilyFrontierTests(unittest.TestCase):
    """A frontier drawn inside a family answers a different question from a joint one."""

    @classmethod
    def setUpClass(cls):
        base = ClassificationTests
        base.setUpClass()
        cls.classification = base.classification

    def test_both_classifications_are_present_and_valid(self):
        for column in ('classification', 'family_classification'):
            self.assertTrue(self.classification[column].isin(
                ['robust frontier', 'conditional frontier', 'dominated']).all(), column)

    def test_a_cell_efficient_jointly_is_efficient_within_its_family(self):
        """Its own family is a subset of the joint set, so it faces fewer rivals."""
        for _, row in self.classification.iterrows():
            if row.classification != 'dominated':
                self.assertNotEqual(row.family_classification, 'dominated', row.strategy)

    def test_the_family_hold_count_is_never_lower_than_the_joint_one(self):
        self.assertTrue((self.classification.family_sensitivities_held
                         >= self.classification.sensitivities_held).all())

    def test_each_family_frontier_has_at_least_one_member(self):
        for family in ('SP500 LEAPS', 'NDX LEAPS'):
            piece = self.classification[self.classification.family == family]
            self.assertTrue((piece.family_classification != 'dominated').any(), family)

    def test_restricting_the_members_restricts_the_comparison(self):
        good, bad = 'SPX_85_30', 'NDX_85_30'
        frame = pd.DataFrame([
            dict(strategy=bad, cagr_p50=.12, cagr_p5=.04, max_drawdown_median=-.60,
                 prob_drawdown_worse_than_60=.40),
            dict(strategy=good, cagr_p50=.18, cagr_p5=.09, max_drawdown_median=-.30,
                 prob_drawdown_worse_than_60=.02)])
        joint = frontier_flags(frame).set_index('strategy')
        alone = frontier_flags(frame, [bad]).set_index('strategy')
        self.assertFalse(bool(joint.loc[bad, 'on_frontier']))
        self.assertTrue(bool(alone.loc[bad, 'on_frontier']))


class EntryPointTests(unittest.TestCase):
    """Running the module is not the same as importing it.

    Every test above imports `letf.leaps_frontier`, which defines the whole
    module before anything runs. `python -m letf.leaps_frontier` executes it top
    to bottom, so a `__main__` guard placed before a function the driver calls
    raises `NameError` at the last step of a half-hour run and no import-based
    test can see it.
    """

    SOURCE = Path(__file__).resolve().parents[1] / 'src/letf/leaps_frontier.py'

    def guard_index(self, tree):
        for index, node in enumerate(tree.body):
            if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                    and getattr(node.test.left, 'id', None) == '__name__'):
                return index
        self.fail('the module has no __main__ guard')

    def test_every_definition_precedes_the_main_guard(self):
        tree = ast.parse(self.SOURCE.read_text())
        guard = self.guard_index(tree)
        late = [node.name for node in tree.body[guard:]
                if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
        self.assertEqual(late, [])

    def test_the_guard_is_the_last_statement(self):
        tree = ast.parse(self.SOURCE.read_text())
        self.assertEqual(self.guard_index(tree), len(tree.body) - 1)

    def test_the_driver_only_calls_names_defined_above_the_guard(self):
        """The specific failure: `run` calling `report`, defined after `main()` ran."""
        tree = ast.parse(self.SOURCE.read_text())
        guard = self.guard_index(tree)
        defined = {node.name for node in tree.body[:guard]
                   if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        driver = [node for node in tree.body[:guard]
                  if isinstance(node, ast.FunctionDef) and node.name in ('run', 'main')]
        self.assertEqual(len(driver), 2)
        for node in driver:
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                    if call.func.id.islower() and call.func.id in TOP_LEVEL_NAMES:
                        self.assertIn(call.func.id, defined, call.func.id)


TOP_LEVEL_NAMES = {node.name for node in
                   ast.parse(EntryPointTests.SOURCE.read_text()).body
                   if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
