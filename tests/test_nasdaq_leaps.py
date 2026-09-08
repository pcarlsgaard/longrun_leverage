import re
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from letf.hedge_alternatives import CRASHES, EQUITY, IV_PREMIUM
from letf.leaps_robustness import PRIMARY_BLOCK, SEED, SLOT, STRUCTURES
from letf.nasdaq_leaps import (ALL_STRUCTURES, MC_HORIZONS, NASDAQ, NDX_PRICE,
                               NDX_STRUCTURES, NDX_TOTAL, NDX_YIELD, OPTION_WEIGHT,
                               PROXY_THROUGH, ROLL_LABEL, UNDERLYING, breakeven_table,
                               build_horizon, figure, historical_table, key_comparison,
                               load, monte_carlo, nasdaq_pool, report, run_path,
                               summarize, underlying_inputs)

ROOT = Path(__file__).resolve().parents[1]


class SpecificationTests(unittest.TestCase):
    def test_only_the_four_prespecified_nasdaq_structures(self):
        self.assertEqual(len(NDX_STRUCTURES), 4)
        self.assertEqual({(m, b) for m, b in NDX_STRUCTURES.values()},
                         {(.80, .20), (.80, .25), (.85, .25), (.85, .30)})

    def test_budgets_stay_inside_the_stated_range(self):
        for moneyness, budget in NDX_STRUCTURES.values():
            self.assertGreaterEqual(budget, .20)
            self.assertLessEqual(budget, .30)
            self.assertIn(moneyness, (.80, .85))

    def test_each_structure_is_mapped_to_its_own_underlying(self):
        self.assertEqual(set(ALL_STRUCTURES), set(STRUCTURES) | set(NDX_STRUCTURES))
        for name in NDX_STRUCTURES:
            self.assertEqual(UNDERLYING[name], NASDAQ)
        for name in STRUCTURES:
            self.assertEqual(UNDERLYING[name], EQUITY)


class InputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.market, cls.signals = underlying_inputs(cls.inputs, ROOT)

    def test_each_index_reads_its_own_dividend_yield(self):
        sp_yield = self.market[EQUITY][1]
        ndx_yield = self.market[NASDAQ][1]
        self.assertFalse(sp_yield.equals(ndx_yield))
        self.assertGreater(sp_yield.mean(), ndx_yield.mean())

    def test_the_nasdaq_yield_is_near_zero_inside_the_proxy_era(self):
        """The proxy assumes no dividends, and the report has to say so."""
        ndx_yield = self.market[NASDAQ][1]
        early = ndx_yield.loc[:PROXY_THROUGH]
        late = ndx_yield.loc[PROXY_THROUGH:]
        self.assertLess(early.mean(), .001)
        self.assertGreater(late.mean(), .003)

    def test_the_nasdaq_is_the_more_volatile_underlying(self):
        self.assertGreater(self.market[NASDAQ][3].mean(), self.market[EQUITY][3].mean())

    def test_both_underlyings_share_one_calendar(self):
        self.assertTrue(self.market[EQUITY][0].index.equals(self.market[NASDAQ][0].index))
        self.assertTrue(self.market[EQUITY][2].equals(self.market[NASDAQ][2]))


class HistoricalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.market, cls.signals = underlying_inputs(cls.inputs, ROOT)
        cls.frame = historical_table(cls.inputs, cls.market, cls.signals)

    def test_every_structure_and_comparator_appears_once(self):
        self.assertEqual(len(self.frame), len(self.frame.strategy.unique()))
        self.assertTrue(set(ALL_STRUCTURES).issubset(set(self.frame.strategy)))

    def test_option_weight_tracks_the_premium_budget(self):
        indexed = self.frame.set_index('strategy')
        for name, (_, budget) in ALL_STRUCTURES.items():
            self.assertAlmostEqual(indexed.loc[name, 'mean_option_weight'], budget,
                                   delta=.08)

    def test_a_nasdaq_structure_reaches_further_on_less_budget(self):
        """The central hypothesis, stated as a test rather than a hope."""
        indexed = self.frame.set_index('strategy')
        nasdaq = indexed.loc['NDX_LEAPS_80_25_TREASURY']
        sp = indexed.loc['LEAPS_85_30_TREASURY']
        self.assertLess(nasdaq.premium_budget, sp.premium_budget)
        self.assertGreater(nasdaq.cagr, sp.cagr)

    def test_the_dot_com_bust_costs_the_nasdaq_structures_more(self):
        indexed = self.frame.set_index('strategy')
        self.assertLess(indexed.loc['NDX_LEAPS_85_30_TREASURY', '2000_2002_bust'],
                        indexed.loc['LEAPS_85_30_TREASURY', '2000_2002_bust'])

    def test_the_1987_column_is_flagged_as_proxy_era(self):
        self.assertTrue(self.frame.stress_1987_inside_proxy_era.all())
        self.assertLessEqual(CRASHES['1987_crash'][1], PROXY_THROUGH)

    def test_post_proxy_cohorts_exist_and_are_a_subset(self):
        self.assertTrue((self.frame.cohort_20y_entries_post_proxy > 0).all())
        for _, row in self.frame.iterrows():
            self.assertGreaterEqual(row.cohort_20y_min_cagr_post_proxy,
                                    row.cohort_20y_min_cagr - 1e-12)

    def test_deeper_budgets_earn_more_and_fall_further(self):
        indexed = self.frame.set_index('strategy')
        for family in (STRUCTURES, NDX_STRUCTURES):
            ordered = sorted(family, key=lambda n: family[n][1])
            rates = [indexed.loc[n, 'cagr'] for n in ordered]
            self.assertEqual(rates, sorted(rates))


class BreakevenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.market, signals = underlying_inputs(cls.inputs, ROOT)
        cls.historical = historical_table(cls.inputs, cls.market, signals)
        cls.frame = breakeven_table(cls.inputs, cls.market, cls.historical)

    def test_a_matched_premium_is_dearer_and_costs_return(self):
        for _, row in self.frame.iterrows():
            self.assertGreater(row.proportionally_matched_premium, IV_PREMIUM)
            self.assertLess(row.cagr_at_matched_premium, row.cagr_at_base_premium)

    def test_the_matched_premium_is_the_volatility_ratio(self):
        for _, row in self.frame.iterrows():
            self.assertAlmostEqual(
                row.proportionally_matched_premium,
                IV_PREMIUM * row.mean_realized_volatility
                / row.sp500_mean_realized_volatility, 12)

    def test_base_cagr_matches_the_historical_table(self):
        rates = self.historical.set_index('strategy')['cagr']
        for _, row in self.frame.iterrows():
            self.assertAlmostEqual(row.cagr_at_base_premium, rates[row.structure], 10)

    def test_a_breakeven_when_present_is_where_the_rival_is_matched(self):
        rates = self.historical.set_index('strategy')['cagr']
        for name in STRUCTURES:
            column = f'{name}_breakeven_premium'
            for _, row in self.frame.iterrows():
                if not np.isfinite(row[column]):
                    continue
                # Above its break-even the structure must have fallen behind.
                self.assertEqual(row.cagr_at_base_premium > rates[name],
                                 row[column] > IV_PREMIUM, (row.structure, name))


class SimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.market, _ = underlying_inputs(cls.inputs, ROOT)
        cls.pool = nasdaq_pool(cls.inputs, cls.market)
        cls.horizon = build_horizon(cls.inputs, 20, (ROLL_LABEL,),
                                    structures=ALL_STRUCTURES)

    def test_pool_carries_three_nasdaq_columns(self):
        self.assertEqual(len(self.pool), len(self.inputs.ix))
        self.assertTrue(np.isfinite(self.pool).all())
        np.testing.assert_allclose(self.pool[:, NDX_TOTAL],
                                   self.inputs.daily.loc[self.inputs.ix, NASDAQ])
        np.testing.assert_allclose(self.pool[:, NDX_YIELD],
                                   self.market[NASDAQ][1].loc[self.inputs.ix])

    def test_the_two_price_columns_are_different_indices(self):
        self.assertFalse(np.allclose(self.pool[:, NDX_PRICE], self.pool[:, 0]))

    def test_one_schedule_per_structure(self):
        self.assertEqual(len(self.horizon.schedules), len(ALL_STRUCTURES))
        for name in ALL_STRUCTURES:
            self.assertIn((name, ROLL_LABEL), self.horizon.schedules)

    def test_every_strategy_is_scored_on_every_path(self):
        sample = self.pool[np.arange(len(self.horizon.calendar) - 1)]
        result = run_path(sample, self.horizon, None)
        self.assertEqual(len(result), 2 + len(ALL_STRUCTURES))
        for name in ALL_STRUCTURES:
            self.assertGreater(result[name][SLOT['mean_delta_exposure']], 0)
            self.assertGreater(result[name][OPTION_WEIGHT], 0)
        self.assertTrue(np.isnan(result[EQUITY][OPTION_WEIGHT]))

    def test_the_nasdaq_structures_use_the_nasdaq_path(self):
        """Swapping the Nasdaq columns must move the Nasdaq rows and nothing else."""
        sample = self.pool[np.arange(len(self.horizon.calendar) - 1)].copy()
        before = run_path(sample, self.horizon, None)
        sample[:, NDX_PRICE] *= .5
        after = run_path(sample, self.horizon, None)
        for name in STRUCTURES:
            np.testing.assert_allclose(before[name], after[name], equal_nan=True)
        for name in NDX_STRUCTURES:
            self.assertFalse(np.allclose(before[name][SLOT['cagr']],
                                         after[name][SLOT['cagr']]))

    def test_parallel_and_serial_agree_exactly(self):
        serial = monte_carlo(self.pool, self.horizon, None, 8, PRIMARY_BLOCK, SEED,
                             workers=1, runner=run_path)
        parallel = monte_carlo(self.pool, self.horizon, None, 8, PRIMARY_BLOCK, SEED,
                               workers=3, runner=run_path)
        for name, values in serial.items():
            np.testing.assert_array_equal(values, parallel[name], err_msg=name)

    def test_summary_is_well_formed(self):
        store = monte_carlo(self.pool, self.horizon, None, 12, PRIMARY_BLOCK, SEED,
                            workers=1, runner=run_path)
        frame = summarize(store, self.horizon).set_index('strategy')
        self.assertEqual(frame.loc[EQUITY, 'prob_below_sp500'], 0.)
        self.assertEqual(frame.loc[NASDAQ, 'prob_below_own_underlying'], 0.)
        for _, row in frame.iterrows():
            self.assertLessEqual(row.cagr_p5, row.cagr_p50)
            self.assertLessEqual(row.cagr_p50, row.cagr_p95)
            self.assertGreaterEqual(row.prob_drawdown_worse_than_50,
                                    row.prob_drawdown_worse_than_60)
            self.assertGreaterEqual(row.prob_drawdown_worse_than_60,
                                    row.prob_drawdown_worse_than_75)


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load(ROOT)
        cls.market, signals = underlying_inputs(cls.inputs, ROOT)
        cls.historical = historical_table(cls.inputs, cls.market, signals)
        cls.breakeven = breakeven_table(cls.inputs, cls.market, cls.historical)
        pool = nasdaq_pool(cls.inputs, cls.market)
        frames = []
        for years in MC_HORIZONS:
            horizon = build_horizon(cls.inputs, years, (ROLL_LABEL,),
                                    structures=ALL_STRUCTURES)
            frames.append(summarize(monte_carlo(pool, horizon, None, 6, PRIMARY_BLOCK,
                                                SEED, workers=1, runner=run_path),
                                    horizon))
        cls.monte = pd.concat(frames, ignore_index=True)
        cls.comparison = key_comparison(cls.monte, cls.historical)

    def render(self, directory):
        report(directory, self.inputs, self.historical, self.breakeven, self.monte,
               self.comparison, 6)
        return (directory / 'nasdaq_leaps_comparison.md').read_text()

    def test_answers_questions_eight_to_fourteen(self):
        with tempfile.TemporaryDirectory() as directory:
            text = self.render(Path(directory))
        for number in range(8, 15):
            self.assertIn(f'**{number}.', text)

    def test_states_both_caveats_before_the_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            text = self.render(Path(directory))
        head = text[:text.index('## Historical')]
        self.assertIn('retuned', head)
        self.assertIn(PROXY_THROUGH, head)

    def test_leaves_no_unfilled_placeholder(self):
        with tempfile.TemporaryDirectory() as directory:
            text = self.render(Path(directory))
        for token in ('{', '}'):
            self.assertNotIn(token, text, token)
        for token in (r'\bnan\b', r'\bNone\b', r'\binf\b'):
            self.assertIsNone(re.search(token, text), token)

    def test_comparison_covers_every_simulated_strategy(self):
        simulated = set(self.monte[self.monte.horizon_years == 30].strategy)
        self.assertEqual(set(self.comparison.strategy), simulated)

    def test_writes_a_figure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nasdaq.png'
            figure(path, self.comparison, self.historical)
            self.assertGreater(path.stat().st_size, 20000)
