import ast
import re
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from letf.hedge_alternatives import CRASHES, EQUITY, EXPIRY_MONTHS, IV_PREMIUM
from letf.leaps_frontier import CAGR_TOLERANCE
from letf.leaps_robustness import PRIMARY_BLOCK, SEED, SLOT as BASE_SLOT, monte_carlo
from letf.leaps_robustness import build_horizon as canonical_horizon
from letf.nasdaq_leaps import NASDAQ, nasdaq_pool, underlying_inputs
from letf.options import YEAR, roll_schedule
from letf.leaps_duration import (CANONICAL, CANONICAL_REGIME, MC_HORIZONS, MC_PATHS,
                                 MENU_SIZE, METRICS, NDX_BUDGETS, NDX_MONEYNESS,
                                 NDX_REGIMES, REGIMES, SLOT, SP_CELLS, SP_REGIMES,
                                 VARIANTS, _invert, build_horizon, classify,
                                 exposure_table, historical_table, load,
                                 matched_frontier, mechanism_table, months,
                                 preferred_region, regime_menu, regime_table, report,
                                 run_path, summarize, frontier_table)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src/letf/leaps_duration.py'
PATHS = 8


class GridTests(unittest.TestCase):
    def test_the_grids_are_the_prespecified_ones(self):
        self.assertEqual(NDX_MONEYNESS, (.80, .85, .90))
        self.assertEqual(NDX_BUDGETS, (.20, .25, .30, .35, .40))
        self.assertEqual(SP_CELLS, {.80: (.25, .30, .35),
                                    .85: (.25, .30, .35, .40, .45),
                                    .90: (.30, .35, .40, .45)})
        self.assertEqual(CANONICAL, (2., 1.))

    def test_no_ninety_five_strike_is_tested(self):
        """The earlier frontier found every one dominated inside the S&P family."""
        self.assertNotIn(.95, SP_CELLS)
        self.assertNotIn(.95, NDX_MONEYNESS)

    def test_the_variant_count_is_the_product_of_the_grids(self):
        nasdaq = len(NDX_MONEYNESS) * len(NDX_BUDGETS) * len(NDX_REGIMES)
        sp = sum(len(b) for b in SP_CELLS.values()) * len(SP_REGIMES)
        self.assertEqual(len(VARIANTS), nasdaq + sp)
        self.assertEqual(sum(1 for v in VARIANTS.values() if v.underlying == NASDAQ),
                         nasdaq)

    def test_nasdaq_stops_at_the_maturity_the_market_lists(self):
        """Beyond fifteen months the only Nasdaq row is the canonical reference."""
        for maturity, roll in NDX_REGIMES:
            self.assertTrue(months(maturity) <= 15 or (maturity, roll) == CANONICAL)

    def test_every_roll_is_coherent_with_its_maturity(self):
        for maturity, roll in SP_REGIMES + NDX_REGIMES:
            self.assertLess(roll, maturity)
            self.assertGreater(maturity - roll, 0)

    def test_the_roll_ladder_is_the_prespecified_one(self):
        by_length = {}
        for maturity, roll in SP_REGIMES:
            by_length.setdefault(months(maturity), set()).add(months(roll))
        self.assertEqual(by_length, {15: {6, 12}, 18: {6, 12}, 24: {6, 12},
                                     30: {12, 18}})

    def test_names_encode_the_specification(self):
        for name, variant in VARIANTS.items():
            prefix, strike, budget, maturity, roll = name.split('_')
            self.assertEqual(prefix, 'SPX' if variant.underlying == EQUITY else 'NDX')
            self.assertEqual(int(strike), round(variant.moneyness * 100))
            self.assertEqual(int(budget), round(variant.budget * 100))
            self.assertEqual(maturity, f'{months(variant.maturity_years)}M')
            self.assertEqual(roll, f'R{months(variant.roll_years)}')

    def test_every_variant_maps_to_a_canonical_twin_in_its_own_cell(self):
        for name, variant in VARIANTS.items():
            twin = VARIANTS[variant.canonical]
            self.assertEqual((twin.underlying, twin.moneyness, twin.budget),
                             (variant.underlying, variant.moneyness, variant.budget))
            self.assertEqual((twin.maturity_years, twin.roll_years), CANONICAL)

    def test_the_rule_keeps_every_inherited_assumption(self):
        for variant in VARIANTS.values():
            rule = variant.rule
            self.assertEqual(rule.iv_premium, IV_PREMIUM)
            self.assertEqual(rule.expiry_months, EXPIRY_MONTHS)
            self.assertEqual(rule.premium_budget, variant.budget)
            self.assertAlmostEqual(rule.roll_at_years,
                                   variant.maturity_years - variant.roll_years)

    def test_path_count_and_horizons_are_prespecified(self):
        self.assertLessEqual(MC_PATHS, 5000)
        self.assertIn(20, MC_HORIZONS)
        self.assertIn(30, MC_HORIZONS)


class InterpolationTests(unittest.TestCase):
    def test_a_target_inside_the_grid_interpolates(self):
        budget, inside = _invert([.5, .6, .7], [.20, .30, .40], .65)
        self.assertTrue(inside)
        self.assertAlmostEqual(budget, .35)

    def test_an_exact_grid_point_returns_it(self):
        budget, inside = _invert([.5, .6, .7], [.20, .30, .40], .6)
        self.assertTrue(inside)
        self.assertAlmostEqual(budget, .30)

    def test_a_target_beyond_the_grid_is_flagged_not_clamped(self):
        low, inside = _invert([.5, .6, .7], [.20, .30, .40], .4)
        self.assertFalse(inside)
        self.assertLess(low, .20)
        high, inside = _invert([.5, .6, .7], [.20, .30, .40], .8)
        self.assertFalse(inside)
        self.assertGreater(high, .40)

    def test_unsorted_input_is_ordered_first(self):
        budget, inside = _invert([.7, .5, .6], [.40, .20, .30], .65)
        self.assertTrue(inside)
        self.assertAlmostEqual(budget, .35)


class CalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.market, _ = underlying_inputs(cls.inputs, ROOT)
        cls.closes = cls.inputs.spot.index
        cls.days = (cls.closes - cls.closes[0]).days.to_numpy().astype(float)

    def entry_years(self, name):
        schedule = roll_schedule(self.closes, VARIANTS[name].rule)
        return (schedule.expiry_days - self.days[schedule.starts]) / YEAR

    def test_each_target_maturity_is_reached_distinctly(self):
        realized = {}
        for maturity in (15, 18, 24, 30):
            name = f'SPX_85_30_{maturity}M_R12'
            realized[maturity] = float(self.entry_years(name).mean())
        self.assertEqual(list(realized.values()), sorted(realized.values()))
        for maturity, got in realized.items():
            self.assertLess(abs(got - maturity / 12), .25, maturity)

    def test_the_boundary_regime_leaves_about_three_months(self):
        schedule = roll_schedule(self.closes, VARIANTS['SPX_85_30_15M_R12'].rule)
        left = (schedule.expiry_days[:-1] - self.days[schedule.starts[1:]]) / YEAR
        self.assertLess(left.mean(), .35)

    def test_a_shorter_roll_buys_more_contracts(self):
        for maturity in (15, 18, 24):
            six = len(roll_schedule(self.closes,
                                    VARIANTS[f'SPX_85_30_{maturity}M_R6'].rule).starts)
            twelve = len(roll_schedule(
                self.closes, VARIANTS[f'SPX_85_30_{maturity}M_R12'].rule).starts)
            self.assertGreater(six, twelve, maturity)

    def test_the_canonical_schedule_is_the_repository_s_own(self):
        theirs = canonical_horizon(self.inputs, 10, ('12m',),
                                   structures={'x': (.85, .30)})
        mine = build_horizon(self.inputs, 10)
        self.assertTrue(mine.calendar.equals(theirs.calendar))
        np.testing.assert_array_equal(mine.days, theirs.days)
        np.testing.assert_array_equal(mine.quarters, theirs.quarters)
        ours = mine.schedules['SPX_85_30_24M_R12']
        np.testing.assert_array_equal(ours.starts, theirs.schedules[('x', '12m')].starts)
        np.testing.assert_array_equal(ours.expiry_days,
                                      theirs.schedules[('x', '12m')].expiry_days)


class TableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.market, _ = underlying_inputs(cls.inputs, ROOT)
        cls.historical = historical_table(cls.inputs, cls.market)
        pool = nasdaq_pool(cls.inputs, cls.market)
        frames = []
        for years in MC_HORIZONS:
            horizon = build_horizon(cls.inputs, years)
            store = monte_carlo(pool, horizon, None, PATHS, PRIMARY_BLOCK, SEED,
                                workers=1, runner=run_path)
            frames.append(summarize(store, horizon, PATHS))
            if years == 30:
                cls.store, cls.horizon = store, horizon
        cls.monte = pd.concat(frames, ignore_index=True)
        cls.exposure = exposure_table(cls.historical, cls.monte)
        cls.matched = matched_frontier(cls.exposure, cls.monte)
        cls.frontier = frontier_table(cls.monte, cls.matched, cls.historical)
        cls.regimes = regime_table(cls.monte, cls.matched, cls.historical)
        cls.verdicts = classify(cls.regimes, cls.exposure)
        cls.mechanism = mechanism_table(cls.historical, cls.regimes, cls.exposure)

    def test_the_historical_table_covers_every_variant(self):
        self.assertEqual(set(self.historical.variant), set(VARIANTS))

    def test_a_longer_contract_carries_more_vega_and_decays_more_slowly(self):
        hist = self.historical.set_index('variant')
        for maturity in (15, 18, 24, 30):
            row = hist.loc[f'SPX_85_30_{maturity}M_R12']
            self.assertGreater(row.vega_per_nav, 0)
            self.assertLess(row.theta_burden_per_year, 0)
        longer = hist.loc['SPX_85_30_30M_R12']
        shorter = hist.loc['SPX_85_30_15M_R12']
        self.assertGreater(longer.vega_per_nav, shorter.vega_per_nav)
        self.assertGreater(longer.theta_burden_per_year, shorter.theta_burden_per_year)

    def test_a_shorter_contract_buys_more_delta_on_the_same_budget(self):
        hist = self.historical.set_index('variant')
        for family, cell in (('SPX', 'SPX_85_30'), ('NDX', 'NDX_85_30')):
            short = hist.loc[f'{cell}_15M_R12', 'mean_delta_exposure']
            canonical = hist.loc[f'{cell}_24M_R12', 'mean_delta_exposure']
            self.assertGreater(short, canonical, family)

    def test_every_stress_episode_is_reported_with_its_exposure(self):
        for event in CRASHES:
            for suffix in ('', '_trough', '_mean_delta', '_min_delta',
                           '_recovery_delta'):
                self.assertIn(f'{event}{suffix}', self.historical.columns)
        self.assertTrue((self.historical['2000_2002_bust'] < 0).all())

    def test_the_canonical_regime_matches_itself_exactly(self):
        rows = self.exposure.set_index('variant')
        for name, variant in VARIANTS.items():
            if name != variant.canonical:
                continue
            self.assertAlmostEqual(rows.loc[name, 'delta_ratio'], 1., places=12)
            self.assertAlmostEqual(rows.loc[name, 'matched_delta_budget'],
                                   variant.budget, places=9)
            self.assertTrue(rows.loc[name, 'matched_budget_inside_grid'])

    def test_a_shorter_contract_needs_a_smaller_budget_to_match(self):
        rows = self.exposure.set_index('variant')
        for cell in ('SPX_85_30', 'NDX_85_30'):
            short = rows.loc[f'{cell}_15M_R12']
            self.assertGreater(short.delta_ratio, 1.)
            self.assertLess(short.matched_delta_budget, short.premium_budget)
            self.assertGreater(short.matched_treasury_weight, short.treasury_weight)

    def test_matched_metrics_reduce_to_the_fixed_ones_at_the_canonical(self):
        thirty = self.monte[self.monte.horizon_years == 30].set_index('variant')
        matched = self.matched.set_index('variant')
        for name, variant in VARIANTS.items():
            if name == variant.canonical and name in matched.index:
                self.assertAlmostEqual(matched.loc[name, 'cagr_p50'],
                                       thirty.loc[name, 'cagr_p50'], places=9)

    def test_the_four_frontiers_are_all_present(self):
        self.assertEqual(set(self.frontier.frontier),
                         {'SPX fixed budget', 'NDX fixed budget',
                          'SPX matched delta', 'NDX matched delta'})
        for _, group in self.frontier.groupby('frontier'):
            self.assertTrue(group.on_frontier.any())

    def test_the_canonical_regime_is_not_compared_against_itself(self):
        self.assertNotIn(CANONICAL_REGIME, set(self.regimes.regime))
        self.assertEqual(len(self.regimes),
                         len(VARIANTS) - sum(1 for v in VARIANTS.values()
                                             if v.regime == CANONICAL_REGIME))

    def test_each_cell_verdict_is_exactly_one_outcome(self):
        for prefix in ('', 'matched_'):
            columns = [f'{prefix}{o}' for o in
                       ('improves', 'worsens', 'mixed', 'unresolved')]
            if columns[0] not in self.regimes:
                continue
            self.assertTrue((self.regimes[columns].sum(axis=1) == 1).all(), prefix)

    def test_the_resolution_bar_is_never_below_the_tolerance(self):
        self.assertTrue((self.regimes.resolution >= CAGR_TOLERANCE).all())

    def test_every_regime_is_classified_once(self):
        self.assertEqual(len(self.verdicts),
                         sum(len(v) - 1 for v in REGIMES.values()))
        self.assertTrue(self.verdicts.classification.isin(
            ['robustly efficient', 'conditionally efficient', 'dominated',
             'unresolved']).all())

    def test_the_mechanism_table_carries_both_gaps(self):
        for _, row in self.mechanism.iterrows():
            self.assertIn('fixed_budget_cagr_gap', row)
            self.assertIn('matched_delta_cagr_gap', row)
            self.assertGreater(row.delta_ratio, 0)

    def test_the_preferred_region_is_asked_inside_each_regime(self):
        chosen = preferred_region(self.monte)
        self.assertEqual(set(chosen), {(family, regime) for family, regimes
                                       in (('SPX', REGIMES[EQUITY]),
                                           ('NDX', REGIMES[NASDAQ]))
                                       for regime in regimes})
        for cells in chosen.values():
            self.assertTrue(cells)

    def test_the_menu_is_small_and_ordered_by_risk(self):
        menu = regime_menu(self.frontier, self.historical)
        self.assertLessEqual(len(menu), MENU_SIZE)
        self.assertEqual(list(menu.median_max_drawdown),
                         sorted(menu.median_max_drawdown, reverse=True))
        self.assertTrue(menu.on_frontier.all())


class PathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = TableTests
        base.setUpClass()
        cls.base = base

    def test_every_variant_and_both_indices_are_scored(self):
        self.assertEqual(set(self.base.store), set(VARIANTS) | {EQUITY, NASDAQ})
        for row in self.base.store.values():
            self.assertEqual(row.shape[1], len(METRICS))

    def test_delta_percentiles_are_ordered(self):
        for name in VARIANTS:
            rows = self.base.store[name]
            self.assertTrue((rows[:, SLOT['delta_p5']]
                             <= rows[:, SLOT['delta_p50']]).all(), name)
            self.assertTrue((rows[:, SLOT['delta_p50']]
                             <= rows[:, SLOT['delta_p95']]).all(), name)

    def test_relative_probabilities_are_paired_not_marginal(self):
        thirty = self.base.monte[self.base.monte.horizon_years == 30
                                 ].set_index('variant')
        name = 'SPX_85_30_15M_R6'
        mine = self.base.store[name][:, BASE_SLOT['terminal_wealth']]
        canonical = self.base.store[VARIANTS[name].canonical][
            :, BASE_SLOT['terminal_wealth']]
        self.assertAlmostEqual(thirty.loc[name, 'prob_below_canonical'],
                               float((mine < canonical).mean()))

    def test_a_canonical_row_does_not_compare_itself_against_itself(self):
        thirty = self.base.monte[self.base.monte.horizon_years == 30
                                 ].set_index('variant')
        for name, variant in VARIANTS.items():
            if name == variant.canonical:
                self.assertEqual(thirty.loc[name, 'prob_below_canonical'], 0.)

    def test_serial_and_parallel_runs_agree(self):
        pool = nasdaq_pool(self.base.inputs, self.base.market)
        horizon = build_horizon(self.base.inputs, 10)
        serial = monte_carlo(pool, horizon, None, 4, PRIMARY_BLOCK, SEED, workers=1,
                             runner=run_path)
        parallel = monte_carlo(pool, horizon, None, 4, PRIMARY_BLOCK, SEED, workers=2,
                               runner=run_path)
        for name in serial:
            np.testing.assert_array_equal(serial[name], parallel[name], name)


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = TableTests
        base.setUpClass()
        cls.base = base

    def text(self):
        with tempfile.TemporaryDirectory() as directory:
            report(Path(directory), self.base.inputs, self.base.historical,
                   self.base.monte, self.base.exposure, self.base.matched,
                   self.base.frontier, self.base.regimes, self.base.verdicts,
                   self.base.mechanism, PATHS)
            return (Path(directory) / 'leaps_duration_roll_results.md').read_text()

    def test_answers_all_ten_questions(self):
        text = self.text()
        for number in range(1, 11):
            self.assertIn(f'**{number}.', text)

    def test_leaves_no_unfilled_placeholder(self):
        text = self.text()
        for token in ('{', '}'):
            self.assertNotIn(token, text, token)
        for token in (r'\bnan\b', r'\binf\b', r'\|\s*None\s*\|'):
            self.assertIsNone(re.search(token, text), token)

    def test_states_the_term_structure_limitation_before_the_numbers(self):
        text = ' '.join(self.text().split())
        head = text[:text.index('## What the listed calendar')]
        self.assertIn('not retuned by maturity', head)
        self.assertIn('term structure', head)

    def test_says_a_fixed_budget_comparison_is_position_size(self):
        text = ' '.join(self.text().split())
        self.assertIn('has been shown to be larger', text)

    def test_names_every_regime(self):
        text = self.text()
        for family, regimes in (('SPX', REGIMES[EQUITY]), ('NDX', REGIMES[NASDAQ])):
            for regime in regimes:
                self.assertIn(regime, text, regime)


class EntryPointTests(unittest.TestCase):
    """Running the module is not the same as importing it."""

    def guard_index(self, tree):
        for index, node in enumerate(tree.body):
            if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                    and getattr(node.test.left, 'id', None) == '__name__'):
                return index
        self.fail('the module has no __main__ guard')

    def test_the_guard_is_the_last_statement(self):
        tree = ast.parse(SOURCE.read_text())
        self.assertEqual(self.guard_index(tree), len(tree.body) - 1)

    def test_every_definition_precedes_the_main_guard(self):
        tree = ast.parse(SOURCE.read_text())
        guard = self.guard_index(tree)
        self.assertEqual([node.name for node in tree.body[guard:]
                          if isinstance(node, (ast.FunctionDef, ast.ClassDef))], [])
