import ast
import re
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from letf.hedge_alternatives import (CRASHES, EXPIRY_MONTHS, IV_PREMIUM,
                                     MATURITY_YEARS, OPTION_SPREAD_BPS)
from letf.leaps_robustness import (PRIMARY_BLOCK, SEED, SLOT as BASE_SLOT, UNDEREXPOSED,
                                   block_draw, monte_carlo)
from letf.leaps_robustness import build_horizon as canonical_horizon
from letf.nasdaq_leaps import NASDAQ, nasdaq_pool, underlying_inputs
from letf.options import YEAR, black_scholes_call, roll_schedule
from letf.xnd_short_maturity import (BRIDGE_MATURITY, CHANNELS, CONTROL, CONTROL_MATURITY,
                                     CONTROL_OF, DIFFERENT, EQUIVALENT, MC_HORIZONS,
                                     MC_PATHS, METRICS, P5_TOLERANCE, SHORT_MATURITY,
                                     SHORT_ROLLS, SLOT, STRUCTURES, TAIL_TOLERANCE,
                                     VARIANTS, VEGA_STEP, build_horizon, classify,
                                     comparison, diagnostics_table, figure, greeks,
                                     held_contract, historical_table, load, recovery,
                                     report, run_path, summarize, verdict)

ROOT = Path(__file__).resolve().parents[1]
PATHS = 8
SOURCE = ROOT / 'src/letf/xnd_short_maturity.py'


class SpecificationTests(unittest.TestCase):
    def test_only_the_two_prespecified_structures(self):
        self.assertEqual(len(STRUCTURES), 2)
        self.assertEqual({(m, b) for m, b in STRUCTURES.values()}, {(.80, .25), (.85, .30)})

    def test_only_the_prespecified_maturities_and_rolls(self):
        self.assertEqual(CONTROL_MATURITY, MATURITY_YEARS)
        self.assertEqual(SHORT_MATURITY, 15 / 12)
        self.assertEqual(SHORT_ROLLS, (.5, .75, 1.))
        self.assertEqual(BRIDGE_MATURITY, 2.25)

    def test_the_variant_set_is_control_plus_three_rolls_plus_bridge(self):
        self.assertEqual(len(VARIANTS), len(STRUCTURES) * (1 + len(SHORT_ROLLS) + 1))
        for structure in STRUCTURES:
            mine = [v for v in VARIANTS.values() if v.structure == structure]
            self.assertEqual(sum(v.family == 'control' for v in mine), 1)
            self.assertEqual(sum(v.family == 'XND 15m' for v in mine), len(SHORT_ROLLS))
            self.assertEqual(sum(v.family == 'QQQ bridge' for v in mine), 1)

    def test_every_short_variant_keeps_its_structure_untouched(self):
        for variant in VARIANTS.values():
            rule = variant.rule
            moneyness, budget = STRUCTURES[variant.structure]
            self.assertEqual(rule.moneyness, moneyness)
            self.assertEqual(rule.premium_budget, budget)
            self.assertEqual(rule.iv_premium, IV_PREMIUM)
            self.assertEqual(rule.spread_bps, OPTION_SPREAD_BPS)
            self.assertEqual(rule.expiry_months, EXPIRY_MONTHS)

    def test_the_roll_is_expressed_as_time_remaining(self):
        for variant in VARIANTS.values():
            self.assertAlmostEqual(variant.rule.roll_at_years,
                                   variant.maturity_years - variant.roll_years)
            self.assertGreater(variant.rule.roll_at_years, 0)

    def test_each_variant_maps_to_its_own_control(self):
        self.assertEqual(len(CONTROL), len(STRUCTURES))
        for name, variant in VARIANTS.items():
            control = VARIANTS[CONTROL_OF[name]]
            self.assertEqual(control.structure, variant.structure)
            self.assertEqual(control.family, 'control')
            self.assertEqual(control.maturity_years, CONTROL_MATURITY)

    def test_the_names_encode_the_specification(self):
        for name, variant in VARIANTS.items():
            maturity, roll = re.search(r'_(\d+)M_ROLL(\d+)M$', name).groups()
            self.assertEqual(int(maturity), round(variant.maturity_years * 12))
            self.assertEqual(int(roll), round(variant.roll_years * 12))

    def test_the_decision_thresholds_are_prespecified(self):
        self.assertEqual((EQUIVALENT, DIFFERENT), (.005, .01))
        self.assertEqual((P5_TOLERANCE, TAIL_TOLERANCE), (.005, .05))
        self.assertEqual(MC_HORIZONS, (10, 20, 30))
        self.assertLessEqual(MC_PATHS, 3000)


class CalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.market, _ = underlying_inputs(cls.inputs, ROOT)
        cls.closes = cls.market[NASDAQ][0].index
        cls.days = (cls.closes - cls.closes[0]).days.to_numpy().astype(float)
        cls.schedules = {name: roll_schedule(cls.closes, v.rule)
                         for name, v in VARIANTS.items()}

    def entry_years(self, name):
        schedule = self.schedules[name]
        return (schedule.expiry_days - self.days[schedule.starts]) / YEAR

    def test_a_fifteen_month_target_buys_a_materially_shorter_contract(self):
        for structure in STRUCTURES:
            stem = structure.replace('NDX_LEAPS_', 'NDX_').replace('_TREASURY', '')
            control = self.entry_years(f'{stem}_24M_ROLL12M').mean()
            for roll in SHORT_ROLLS:
                short = self.entry_years(f'{stem}_15M_ROLL{round(roll * 12)}M').mean()
                self.assertLess(short, control - .5)
                self.assertGreater(short, .9)

    def test_the_twelve_month_roll_is_the_boundary_condition_it_claims_to_be(self):
        """About three months left at the roll is the whole point of including it."""
        for structure in STRUCTURES:
            stem = structure.replace('NDX_LEAPS_', 'NDX_').replace('_TREASURY', '')
            schedule = self.schedules[f'{stem}_15M_ROLL12M']
            left = (schedule.expiry_days[:-1] - self.days[schedule.starts[1:]]) / YEAR
            self.assertLess(left.mean(), .35)
            self.assertGreater(left.mean(), .15)

    def test_a_shorter_roll_buys_more_contracts(self):
        for structure in STRUCTURES:
            stem = structure.replace('NDX_LEAPS_', 'NDX_').replace('_TREASURY', '')
            counts = [len(self.schedules[f'{stem}_15M_ROLL{round(r * 12)}M'].starts)
                      for r in sorted(SHORT_ROLLS)]
            self.assertEqual(counts, sorted(counts, reverse=True))

    def test_the_control_schedule_is_the_repository_s_own(self):
        """The control must be the existing rule, not a re-spelling of it."""
        horizon = canonical_horizon(self.inputs, 10, ('12m',), structures=STRUCTURES)
        mine = build_horizon(self.inputs, 10)
        self.assertTrue(mine.calendar.equals(horizon.calendar))
        self.assertTrue(mine.closes.equals(horizon.closes))
        np.testing.assert_array_equal(mine.days, horizon.days)
        np.testing.assert_array_equal(mine.quarters, horizon.quarters)
        for structure in STRUCTURES:
            stem = structure.replace('NDX_LEAPS_', 'NDX_').replace('_TREASURY', '')
            theirs = horizon.schedules[(structure, '12m')]
            ours = mine.schedules[f'{stem}_24M_ROLL12M']
            np.testing.assert_array_equal(ours.starts, theirs.starts)
            np.testing.assert_array_equal(ours.expiry_days, theirs.expiry_days)


class GreekTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.market, _ = underlying_inputs(cls.inputs, ROOT)
        cls.closes = cls.market[NASDAQ][0].index
        cls.spot = cls.market[NASDAQ][0].to_numpy()
        cls.days = (cls.closes - cls.closes[0]).days.to_numpy().astype(float)

    def test_the_held_contract_covers_every_session_from_the_first_purchase(self):
        schedule = roll_schedule(self.closes, VARIANTS[list(VARIANTS)[0]].rule)
        strike, expiry = held_contract(schedule, self.spot, self.days)
        first = int(schedule.starts[0])
        self.assertTrue(np.isfinite(strike[first:]).all())
        self.assertTrue(np.isfinite(expiry[first:]).all())
        self.assertTrue(np.isnan(strike[:first]).all())

    def test_a_roll_session_belongs_to_the_contract_bought_there(self):
        variant = VARIANTS[list(VARIANTS)[0]]
        schedule = roll_schedule(self.closes, variant.rule)
        strike, _ = held_contract(schedule, self.spot, self.days)
        for start in schedule.starts:
            self.assertAlmostEqual(strike[start],
                                   variant.rule.moneyness * self.spot[start])

    def test_the_finite_difference_vega_matches_the_closed_form(self):
        """Pinned against Black-Scholes rather than trusted as a numerical recipe."""
        rng = np.random.default_rng(11)
        spot = rng.uniform(50, 5000, 400)
        strike = spot * rng.uniform(.6, 1.3, 400)
        rate, dividend = rng.uniform(0, .08, 400), rng.uniform(0, .04, 400)
        vol, years = rng.uniform(.08, .9, 400), rng.uniform(.05, 3., 400)
        up = black_scholes_call(spot, strike, rate, dividend, vol + VEGA_STEP, years)
        down = black_scholes_call(spot, strike, rate, dividend, vol - VEGA_STEP, years)
        numeric = (up - down) / (2 * VEGA_STEP)
        d1 = ((np.log(spot / strike) + (rate - dividend + vol ** 2 / 2) * years)
              / (vol * np.sqrt(years)))
        closed = spot * np.exp(-dividend * years) * norm.pdf(d1) * np.sqrt(years)
        np.testing.assert_allclose(numeric, closed, rtol=1e-6, atol=1e-8)

    def test_time_decays_value_and_volatility_adds_it(self):
        for name, variant in VARIANTS.items():
            measured = greeks(self.market, roll_schedule(self.closes, variant.rule))
            self.assertTrue((measured['theta_per_value'] <= 1e-12).all(), name)
            self.assertTrue((measured['vega_per_value'] >= -1e-12).all(), name)

    def test_a_shorter_contract_decays_faster_and_carries_less_vega(self):
        for structure in STRUCTURES:
            stem = structure.replace('NDX_LEAPS_', 'NDX_').replace('_TREASURY', '')
            control = greeks(self.market, roll_schedule(
                self.closes, VARIANTS[f'{stem}_24M_ROLL12M'].rule))
            short = greeks(self.market, roll_schedule(
                self.closes, VARIANTS[f'{stem}_15M_ROLL12M'].rule))
            self.assertLess(short['theta_per_value'].mean(),
                            control['theta_per_value'].mean())
            self.assertLess(short['vega_per_value'].mean(),
                            control['vega_per_value'].mean())
            self.assertLess(short['remaining'].mean(), control['remaining'].mean())


class RecoveryTests(unittest.TestCase):
    def test_a_path_that_never_falls_thirty_percent_reports_nothing(self):
        navs = np.linspace(1., 2., 900)
        out = recovery(np.full(900, 1.2), navs)
        self.assertEqual(out['sessions_after_major_loss'], 0)
        self.assertFalse(out['underexposed_recovery'])
        self.assertTrue(np.isnan(out['recovery_exposure']))

    def test_a_flat_exposure_through_a_crash_is_not_underexposed(self):
        navs = np.r_[np.linspace(1., 2., 300), np.linspace(2., 1., 300),
                     np.linspace(1., 2.5, 400)]
        out = recovery(np.full(len(navs), 1.5), navs)
        self.assertGreater(out['sessions_after_major_loss'], 0)
        self.assertAlmostEqual(out['recovery_exposure_ratio'], 1.)
        self.assertFalse(out['underexposed_recovery'])

    def test_exposure_that_collapses_after_the_trough_is_flagged(self):
        navs = np.r_[np.linspace(1., 2., 300), np.linspace(2., 1., 300),
                     np.linspace(1., 2.5, 400)]
        exposures = np.r_[np.full(600, 1.5), np.full(400, .2)]
        out = recovery(exposures, navs)
        self.assertTrue(out['underexposed_recovery'])
        self.assertLess(out['recovery_exposure_ratio'], UNDEREXPOSED)


class HistoricalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.market, _ = underlying_inputs(cls.inputs, ROOT)
        cls.historical = historical_table(cls.inputs, cls.market).set_index('variant')

    def test_it_covers_every_variant(self):
        self.assertEqual(set(self.historical.index), set(VARIANTS))

    def test_the_premium_paid_is_the_budget_it_claims(self):
        for name, variant in VARIANTS.items():
            self.assertAlmostEqual(
                self.historical.loc[name, 'mean_premium_fraction_of_nav'],
                STRUCTURES[variant.structure][1], places=9)

    def test_a_shorter_roll_trades_more_and_pays_more_spread(self):
        for structure in STRUCTURES:
            stem = structure.replace('NDX_LEAPS_', 'NDX_').replace('_TREASURY', '')
            six = self.historical.loc[f'{stem}_15M_ROLL6M']
            twelve = self.historical.loc[f'{stem}_15M_ROLL12M']
            self.assertGreater(six.option_turnover_per_year, twelve.option_turnover_per_year)
            self.assertGreater(six.cost_drag_bps, twelve.cost_drag_bps)

    def test_the_control_matches_the_committed_nasdaq_result(self):
        """The control must be the published structure, not a near-miss of it."""
        published = pd.read_csv(ROOT / 'reports/nasdaq_leaps_historical.csv'
                                ).set_index('strategy')
        for structure in STRUCTURES:
            stem = structure.replace('NDX_LEAPS_', 'NDX_').replace('_TREASURY', '')
            mine = self.historical.loc[f'{stem}_24M_ROLL12M']
            theirs = published.loc[structure]
            for column in ('cagr', 'max_drawdown', 'mean_delta_exposure',
                           'mean_option_weight', '2000_2002_bust'):
                self.assertAlmostEqual(mine[column] / theirs[column], 1., places=7,
                                       msg=f'{structure} {column}')

    def test_the_delta_percentiles_are_ordered(self):
        for name in VARIANTS:
            row = self.historical.loc[name]
            self.assertLessEqual(row.delta_exposure_p5, row.delta_exposure_p50)
            self.assertLessEqual(row.delta_exposure_p50, row.delta_exposure_p95)
            self.assertLessEqual(row.min_exposure_after_loss, row.delta_exposure_p95)

    def test_theta_is_a_drag_and_vega_is_positive(self):
        self.assertTrue((self.historical.theta_burden_per_year < 0).all())
        self.assertTrue((self.historical.vega_per_nav > 0).all())

    def test_the_dot_com_bust_is_present_and_negative_for_every_variant(self):
        self.assertTrue((self.historical['2000_2002_bust'] < 0).all())
        for event in CRASHES:
            self.assertTrue(self.historical[event].notna().all(), event)


class MonteCarloTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.market, _ = underlying_inputs(cls.inputs, ROOT)
        cls.pool = nasdaq_pool(cls.inputs, cls.market)
        cls.horizon = build_horizon(cls.inputs, 10)
        cls.store = monte_carlo(cls.pool, cls.horizon, None, PATHS, PRIMARY_BLOCK, SEED,
                                workers=1, runner=run_path)
        cls.frame = summarize(cls.store, cls.horizon, PATHS)

    def test_every_variant_and_the_index_are_scored(self):
        self.assertEqual(set(self.store), set(VARIANTS) | {NASDAQ})
        for row in self.store.values():
            self.assertEqual(row.shape[1], len(METRICS))

    def test_one_path_carries_every_variant(self):
        rng = np.random.default_rng(5)
        sample = self.pool[block_draw(rng, len(self.pool),
                                      len(self.horizon.calendar) - 1, PRIMARY_BLOCK)]
        result = run_path(sample, self.horizon, None)
        self.assertEqual(set(result), set(VARIANTS) | {NASDAQ})
        for name in VARIANTS:
            row = result[name]
            self.assertLessEqual(row[SLOT['delta_p5']], row[SLOT['delta_p50']])
            self.assertLessEqual(row[SLOT['delta_p50']], row[SLOT['delta_p95']])
            self.assertGreater(row[SLOT['turnover_per_year']], 0)

    def test_a_shorter_roll_turns_over_more_on_the_same_path(self):
        rows = self.frame.set_index('variant')
        for structure in STRUCTURES:
            stem = structure.replace('NDX_LEAPS_', 'NDX_').replace('_TREASURY', '')
            self.assertGreater(rows.loc[f'{stem}_15M_ROLL6M', 'turnover_per_year'],
                               rows.loc[f'{stem}_15M_ROLL12M', 'turnover_per_year'])

    def test_percentiles_are_ordered_and_probabilities_are_probabilities(self):
        for _, row in self.frame.iterrows():
            self.assertLessEqual(row.cagr_p5, row.cagr_p10)
            self.assertLessEqual(row.cagr_p10, row.cagr_p50)
            self.assertLessEqual(row.cagr_p50, row.cagr_p90)
            self.assertLessEqual(row.cagr_p90, row.cagr_p95)
        for column in [c for c in self.frame.columns if c.startswith('prob_')]:
            self.assertTrue(self.frame[column].between(0, 1).all(), column)

    def test_relative_probabilities_are_paired_not_marginal(self):
        rows = self.frame.set_index('variant')
        name = [n for n, v in VARIANTS.items() if v.family == 'XND 15m'][0]
        mine = self.store[name][:, BASE_SLOT['terminal_wealth']]
        control = self.store[CONTROL_OF[name]][:, BASE_SLOT['terminal_wealth']]
        index = self.store[NASDAQ][:, BASE_SLOT['terminal_wealth']]
        self.assertAlmostEqual(rows.loc[name, 'prob_below_control'],
                               float((mine < control).mean()))
        self.assertAlmostEqual(rows.loc[name, 'prob_below_nasdaq'],
                               float((mine < index).mean()))

    def test_a_control_does_not_compare_itself_against_itself(self):
        rows = self.frame.set_index('variant')
        for control in CONTROL.values():
            self.assertEqual(rows.loc[control, 'prob_below_control'], 0.)

    def test_serial_and_parallel_runs_agree(self):
        parallel = monte_carlo(self.pool, self.horizon, None, 4, PRIMARY_BLOCK, SEED,
                               workers=2, runner=run_path)
        serial = monte_carlo(self.pool, self.horizon, None, 4, PRIMARY_BLOCK, SEED,
                             workers=1, runner=run_path)
        for name in serial:
            np.testing.assert_array_equal(serial[name], parallel[name], name)


class DecisionTests(unittest.TestCase):
    """The three-way rule, exercised on rows built to hit each branch."""

    NAME = next(n for n, v in VARIANTS.items() if v.family == 'XND 15m')

    def row(self, **overrides):
        base = dict(variant=self.NAME, cagr_p50_20y=0., cagr_p50_30y=0., cagr_p5_20y=0.,
                    cagr_p5_30y=0., median_max_drawdown_20y=0., median_max_drawdown_30y=0.,
                    prob_drawdown_worse_than_60_20y=0.,
                    prob_drawdown_worse_than_60_30y=0., underexposed_recovery_20y=0.,
                    underexposed_recovery_30y=0., turnover_ratio=1.,
                    cost_drag_difference_bps=0.)
        base.update(overrides)
        return base

    def historical(self, ratio=1.):
        return pd.DataFrame([dict(variant=self.NAME, recovery_exposure_ratio=ratio)])

    def test_no_movement_is_practically_equivalent(self):
        answer, reason = verdict(self.row(), self.historical())
        self.assertEqual(answer, 'practically equivalent')
        self.assertIn('tolerance', reason)

    def test_a_small_loss_with_more_turnover_is_acceptable_but_different(self):
        answer, reason = verdict(self.row(cagr_p50_30y=-.007, turnover_ratio=2.),
                                 self.historical())
        self.assertEqual(answer, 'acceptable but different')
        self.assertIn('turnover', reason)

    def test_a_large_loss_is_not_equivalent(self):
        answer, reason = verdict(self.row(cagr_p50_30y=-.02), self.historical())
        self.assertEqual(answer, 'not implementation-equivalent')
        self.assertIn('median CAGR falls', reason)

    def test_a_fifth_percentile_collapse_alone_is_not_equivalent(self):
        answer, reason = verdict(self.row(cagr_p5_20y=-.02), self.historical())
        self.assertEqual(answer, 'not implementation-equivalent')
        self.assertIn('fifth percentile', reason)

    def test_a_tail_probability_jump_alone_is_not_equivalent(self):
        answer, reason = verdict(self.row(prob_drawdown_worse_than_60_30y=.20),
                                 self.historical())
        self.assertEqual(answer, 'not implementation-equivalent')
        self.assertIn('P(DD>60%)', reason)

    def test_unstable_recovery_alone_is_not_equivalent(self):
        answer, reason = verdict(self.row(), self.historical(ratio=.5))
        self.assertEqual(answer, 'not implementation-equivalent')
        self.assertIn('unstable', reason)

    def test_the_worse_of_the_two_horizons_decides(self):
        """Matching over thirty years and not over twenty has not matched."""
        answer, _ = verdict(self.row(cagr_p50_20y=-.02, cagr_p50_30y=.001),
                            self.historical())
        self.assertEqual(answer, 'not implementation-equivalent')

    def test_a_gain_is_never_penalised_as_a_loss(self):
        answer, _ = verdict(self.row(cagr_p50_20y=.02, cagr_p50_30y=.02),
                            self.historical())
        self.assertEqual(answer, 'practically equivalent')


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
        cls.monte = pd.concat(frames, ignore_index=True)
        cls.compared = classify(comparison(cls.monte, cls.historical), cls.historical)
        cls.diagnostics = diagnostics_table(cls.historical, cls.monte)

    def test_the_comparison_covers_every_non_control_variant(self):
        expected = {n for n, v in VARIANTS.items() if v.family != 'control'}
        self.assertEqual(set(self.compared.variant), expected)
        self.assertTrue(self.compared.classification.isin(
            ['practically equivalent', 'acceptable but different',
             'not implementation-equivalent']).all())

    def test_the_comparison_is_signed_against_the_control(self):
        rows = self.compared.set_index('variant')
        thirty = self.monte[self.monte.horizon_years == 30].set_index('variant')
        for name in rows.index:
            self.assertAlmostEqual(
                rows.loc[name, 'cagr_p50_30y'],
                thirty.loc[name, 'cagr_p50'] - thirty.loc[CONTROL_OF[name], 'cagr_p50'])

    def test_the_turnover_ratio_is_measured_not_assumed(self):
        hist = self.historical.set_index('variant')
        rows = self.compared.set_index('variant')
        for name in rows.index:
            self.assertAlmostEqual(
                rows.loc[name, 'turnover_ratio'],
                hist.loc[name, 'option_turnover_per_year']
                / hist.loc[CONTROL_OF[name], 'option_turnover_per_year'])

    def test_every_channel_is_reported_for_every_variant(self):
        for name in VARIANTS:
            piece = self.diagnostics[self.diagnostics.variant == name]
            self.assertEqual(set(piece.channel), set(CHANNELS))
            for channel, columns in CHANNELS.items():
                metrics = set(piece[piece.channel == channel].metric)
                self.assertTrue(set(columns) <= metrics, channel)

    def test_a_control_is_its_own_reference_in_the_diagnostics(self):
        for control in CONTROL.values():
            piece = self.diagnostics[self.diagnostics.variant == control]
            finite = piece[piece.value.notna() & piece.control_value.notna()]
            np.testing.assert_allclose(finite.difference, 0., atol=1e-12)


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = TableTests
        base.setUpClass()
        cls.base = base

    def text(self):
        with tempfile.TemporaryDirectory() as directory:
            report(Path(directory), self.base.inputs, self.base.historical,
                   self.base.monte, self.base.compared, self.base.diagnostics, PATHS)
            return (Path(directory) / 'xnd_short_maturity_results.md').read_text()

    def test_answers_all_eight_questions(self):
        text = self.text()
        for number in range(1, 9):
            self.assertIn(f'**{number}.', text)

    def test_leaves_no_unfilled_placeholder(self):
        text = self.text()
        for token in ('{', '}'):
            self.assertNotIn(token, text, token)
        for token in (r'\bnan\b', r'\bNone\b', r'\binf\b'):
            self.assertIsNone(re.search(token, text), token)

    def test_names_every_variant(self):
        text = self.text()
        for name in VARIANTS:
            self.assertIn(name, text, name)

    def test_states_the_term_structure_caveat_and_the_bridge_s_limits(self):
        text = ' '.join(self.text().split())
        self.assertIn('term structure', text)
        self.assertIn('American exercise', text)
        self.assertIn('not re-estimated', text.replace('**not**', 'not'))

    def test_calls_the_twelve_month_roll_a_boundary_condition(self):
        self.assertIn('boundary condition', self.text())

    def test_the_figure_renders(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'xnd.png'
            figure(path, self.base.monte, self.base.historical)
            self.assertGreater(path.stat().st_size, 10000)


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
