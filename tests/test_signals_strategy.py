import unittest

import numpy as np
import pandas as pd

from letf.cohort_distributions import PERCENTILES, percentile_row
from letf.cross_index_signal import cohort_percentiles
from letf.signals import (discrete_exposure, level_position, sma_position,
                          signal_price_return, volatility_position)
from letf.strategy import (apply_costs, rotation_turnover, select_returns,
                           switching_costs, transitions)


class SignalTests(unittest.TestCase):
    def setUp(self):
        self.ix = pd.bdate_range('2020-01-01', periods=400)

    def test_price_and_total_return_signals_can_disagree(self):
        """The reason the repo switched conventions: they cross on different days."""
        rng = np.random.default_rng(11)
        r = pd.Series(rng.normal(.0004, .01, len(self.ix)), index=self.ix)
        price = (1 + r).cumprod()
        dividend = 0.02 / 252
        total = (1 + r + dividend).cumprod().pct_change(fill_method=None).dropna()
        a = level_position(price, self.ix, 200, 1)
        b = sma_position(total, self.ix, 200)
        common = a.dropna().index.intersection(b.dropna().index)
        self.assertTrue(len(common) > 100)
        self.assertTrue((a.loc[common] != b.loc[common]).any(),
                        'conventions must be able to differ, else the correction is moot')

    def test_no_position_before_the_lookback_completes(self):
        price = pd.Series(np.arange(1, len(self.ix) + 1, dtype=float), index=self.ix)
        for lag in (1, 2):
            p = level_position(price, self.ix, 200, lag)
            self.assertTrue(p.iloc[:200 + lag - 1].isna().all())
            self.assertFalse(np.isnan(p.iloc[200 + lag - 1]))

    def test_signal_cannot_see_the_return_it_governs(self):
        price = pd.Series(np.arange(1, len(self.ix) + 1, dtype=float), index=self.ix)
        bumped = price.copy()
        bumped.iloc[300:] *= 3
        for lag in (1, 2):
            a = level_position(price, self.ix, 200, lag)
            b = level_position(bumped, self.ix, 200, lag)
            pd.testing.assert_series_equal(a.iloc[:300 + lag], b.iloc[:300 + lag])

    def test_signal_price_return_is_plain_close_to_close(self):
        price = pd.Series([100., 110., 99.], index=self.ix[:3])
        np.testing.assert_allclose(signal_price_return(price).dropna(), [.10, -.10])

    def test_exposure_ladder_is_discrete_and_bounded(self):
        desired = pd.Series([0.4, 1.4, 1.6, 2.4, 2.6, 9.0, np.nan], index=self.ix[:7])
        np.testing.assert_array_equal(discrete_exposure(desired).dropna(),
                                      [1., 1., 2., 2., 3., 3.])

    def test_volatility_state_and_estimate_are_both_lagged(self):
        rng = np.random.default_rng(5)
        r = pd.Series(rng.normal(0, .01, 100), index=self.ix[:100])
        s1, v1 = volatility_position(r, window=20, lag=1)
        s2, v2 = volatility_position(r, window=20, lag=2)
        pd.testing.assert_series_equal(s2, s1.shift(1))
        pd.testing.assert_series_equal(v2, v1.shift(1))
        self.assertTrue(v1.iloc[:20].isna().all())

    def test_rejects_unsupported_lag_and_nonpositive_levels(self):
        price = pd.Series(np.arange(1, len(self.ix) + 1, dtype=float), index=self.ix)
        with self.assertRaises(ValueError):
            level_position(price, self.ix, 200, 3)
        bad = price.copy(); bad.iloc[5] = 0.
        with self.assertRaises(ValueError):
            level_position(bad, self.ix, 200, 1)


class CostTests(unittest.TestCase):
    def setUp(self):
        self.ix = pd.bdate_range('2020-01-01', periods=6)

    def test_a_rotation_is_turnover_one_per_switch(self):
        p = pd.Series([1., 1, 0, 0, 1, 1], index=self.ix)
        np.testing.assert_array_equal(rotation_turnover(p), [0, 0, 1, 0, 1, 0])
        self.assertEqual(transitions(p).sum(), 2)

    def test_switching_costs_is_apply_costs_at_rotation_turnover(self):
        p = pd.Series([1., 1, 0, 0, 1, 1], index=self.ix)
        r = pd.Series([.1, -.1, .02, 0, .05, -.02], index=self.ix)
        pd.testing.assert_series_equal(switching_costs(r, p, 50),
                                       apply_costs(r, rotation_turnover(p), 50))

    def test_partial_turnover_costs_proportionally_less(self):
        r = pd.Series(0., index=self.ix)
        full = apply_costs(r, pd.Series([0., 0, 1, 0, 0, 0], index=self.ix), 50)
        half = apply_costs(r, pd.Series([0., 0, .5, 0, 0, 0], index=self.ix), 50)
        self.assertAlmostEqual(full.iloc[2], -.005)
        self.assertAlmostEqual(half.iloc[2], -.0025)

    def test_round_trip_compounds_rather_than_adding(self):
        p = pd.Series([1., 1, 0, 0, 1, 1], index=self.ix)
        r = pd.Series(0., index=self.ix)
        self.assertAlmostEqual((1 + switching_costs(r, p, 50)).prod(), .995 ** 2)

    def test_negative_turnover_and_bad_cost_are_rejected(self):
        r = pd.Series(0., index=self.ix)
        with self.assertRaises(ValueError):
            apply_costs(r, pd.Series(-1., index=self.ix), 50)
        with self.assertRaises(ValueError):
            apply_costs(r, pd.Series(0., index=self.ix), -1)

    def test_cannot_buy_a_terminated_sleeve(self):
        daily = pd.DataFrame({'LEV': [0., -1., 0., 0., 0., 0.], 'CASH': 0.}, index=self.ix)
        p = pd.Series([0., 1, 0, 0, 1, 0], index=self.ix)
        with self.assertRaises(ValueError):
            select_returns(daily, p, {0: 'CASH', 1: 'LEV'})


class CohortReportingTests(unittest.TestCase):
    """The two newest modules, which previously had no tests at all."""

    def setUp(self):
        self.calendar = pd.bdate_range('1990-01-01', '2025-12-31')
        rng = np.random.default_rng(13)
        self.r = pd.Series(rng.normal(.0004, .01, len(self.calendar) - 1),
                           index=self.calendar[1:])

    def test_percentile_row_is_ordered_and_bracketed(self):
        row = percentile_row('X', 'SP500', 1, 25, 20, self.r, self.calendar)
        values = [row[f'p{p}'] for p in (1, 10, 25, 50, 75, 90, 99)]
        self.assertEqual(values, sorted(values))
        self.assertLessEqual(row['min'], values[0])
        self.assertGreaterEqual(row['max'], values[-1])
        self.assertGreater(row['cohort_count'], 100)
        self.assertEqual(len(PERCENTILES), 7)

    def test_percentile_row_reports_empty_cohorts_as_nan_not_zero(self):
        row = percentile_row('X', 'SP500', 1, 0, 90, self.r, self.calendar)
        self.assertEqual(row['cohort_count'], 0)
        self.assertTrue(np.isnan(row['p50']))
        self.assertTrue(np.isnan(row['min']))

    def test_cross_index_cohort_percentiles_cover_every_horizon(self):
        rows = cohort_percentiles(self.r, self.calendar)
        self.assertEqual([r['horizon_years'] for r in rows], [10, 20, 30])
        for row in rows:
            self.assertGreater(row['cohorts'], 0)
            self.assertLessEqual(row['p10'], row['p50'])
            self.assertLessEqual(row['p50'], row['p90'])
        self.assertGreater(rows[0]['cohorts'], rows[2]['cohorts'])

    def test_both_modules_agree_on_the_same_cohorts(self):
        """Different reports must not disagree about the same distribution."""
        row = percentile_row('X', 'SP500', 1, 0, 20, self.r, self.calendar)
        cross = [r for r in cohort_percentiles(self.r, self.calendar) if r['horizon_years'] == 20][0]
        self.assertEqual(row['cohort_count'], cross['cohorts'])
        self.assertAlmostEqual(row['p50'], cross['p50'])


if __name__ == '__main__':
    unittest.main()
