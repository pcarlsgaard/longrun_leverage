import unittest
import pandas as pd
import numpy as np

from letf.model import simulate, wealth, calendar_days, financing_accrual, portfolio, fit_spread


class ModelTests(unittest.TestCase):
    def inputs(self, values):
        index = pd.bdate_range("2020-01-02", periods=len(values))
        return pd.Series(values, index=index), pd.Series(1.0, index=index), pd.Series(0.0, index=index)

    def test_compounding_is_daily_not_period_multiplier(self):
        r, days, funding = self.inputs([.1, .1])
        self.assertAlmostEqual(wealth(simulate(r, days, funding, 3, 0, 0)).iloc[-1], 1.69)

    def test_round_trip_generates_drag_without_a_decay_fee(self):
        r, days, funding = self.inputs([.1, 1 / 1.1 - 1])
        self.assertAlmostEqual(wealth(r).iloc[-1], 1)
        self.assertAlmostEqual(wealth(simulate(r, days, funding, 3, 0, 0)).iloc[-1], 1.3 * (1 - 3 / 11))

    def test_weekend_funding_and_rate_lag(self):
        ix = pd.DatetimeIndex(["2024-01-08"])
        previous = pd.Timestamp("2024-01-05")
        rates = pd.Series([.036, .072, .108, .9], index=pd.date_range("2024-01-05", periods=4))
        days = calendar_days(ix, previous)
        funding = financing_accrual(ix, previous, rates)
        self.assertEqual(days.iloc[0], 3)
        self.assertAlmostEqual(funding.iloc[0], (.036 + .072 + .108) / 360)
        r = pd.Series(0.0, index=ix)
        self.assertAlmostEqual(simulate(r, days, funding, 3, .01, .005).iloc[0],
                               -2 * ((.036 + .072 + .108) / 360 + .005 * 3 / 360) - .01 * 3 / 365)

    def test_unleveraged_does_not_borrow(self):
        r, days, funding = self.inputs([.01, -.02])
        np.testing.assert_allclose(simulate(r, days, funding + .03, 1, 0, .09), r)

    def test_zero_nav_is_absorbing(self):
        r, days, funding = self.inputs([-.4, .9, .1])
        np.testing.assert_allclose(wealth(simulate(r, days, funding, 3, 0, 0)), [0, 0, 0])

    def test_missing_data_fails(self):
        r, days, funding = self.inputs([.1, np.nan])
        with self.assertRaises(ValueError):
            simulate(r, days, funding, 3, 0)

    def test_buy_hold_and_rebalance_differ(self):
        ix = pd.to_datetime(["2020-01-31", "2020-02-03"])
        r = pd.DataFrame({"a": [1., -0.5], "b": [0., 0.]}, index=ix)
        weights = {"a": .5, "b": .5}
        self.assertAlmostEqual(wealth(portfolio(r, weights, "never")).iloc[-1], 1)
        self.assertAlmostEqual(wealth(portfolio(r, weights, "monthly")).iloc[-1], 1.125)

    def test_cannot_rebuy_terminated_fund(self):
        ix = pd.to_datetime(["2020-01-31", "2020-02-03"])
        r = pd.DataFrame({"a": [-1., 0.], "b": [0., 0.]}, index=ix)
        with self.assertRaises(ValueError):
            portfolio(r, {"a": .5, "b": .5}, "monthly")

    def test_recover_known_training_drag(self):
        r, days, funding = self.inputs([.02, -.03, .01, -.01] * 30)
        actual = simulate(r, days, funding + .0001, 3, .009, .007)
        fit = fit_spread(r, actual, days, funding + .0001, 3, .009)
        self.assertAlmostEqual(fit, .007, places=9)


if __name__ == "__main__":
    unittest.main()
