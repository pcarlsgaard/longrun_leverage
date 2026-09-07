import unittest

import numpy as np
import pandas as pd

from letf.options import (LEDGER_COLUMNS, LeapsRule, black_scholes_call,
                          break_even_iv_premium, call_delta, choose_expiry,
                          implied_volatility_proxy, leaps_arrays, listed_expiries,
                          roll_schedule, simulate_leaps_arrays, simulate_leaps_portfolio,
                          trailing_dividend_yield, trailing_riskfree)


class BlackScholesTests(unittest.TestCase):
    def test_matches_published_values(self):
        self.assertAlmostEqual(float(black_scholes_call(100, 100, .05, 0, .2, 1)), 10.450584, 5)
        self.assertAlmostEqual(float(call_delta(100, 100, .05, 0, .2, 1)), .636831, 5)

    def test_put_call_parity_holds(self):
        spot, strike, rate, div, vol, years = 100., 80., .04, .02, .25, 2.
        call = float(black_scholes_call(spot, strike, rate, div, vol, years))
        put = call - spot * np.exp(-div * years) + strike * np.exp(-rate * years)
        # Reprice the put independently through the symmetric formula.
        forward = spot * np.exp((rate - div) * years)
        sigma = vol * np.sqrt(years)
        d1 = np.log(forward / strike) / sigma + sigma / 2
        from scipy.special import ndtr
        expected = np.exp(-rate * years) * (strike * ndtr(sigma - d1) - forward * ndtr(-d1))
        self.assertAlmostEqual(put, float(expected), 10)

    def test_expiry_pays_intrinsic(self):
        self.assertEqual(float(black_scholes_call(120, 100, .04, .02, .25, 0)), 20.)
        self.assertEqual(float(black_scholes_call(80, 100, .04, .02, .25, 0)), 0.)

    def test_zero_volatility_is_discounted_forward_intrinsic(self):
        value = float(black_scholes_call(100, 80, .04, .02, 0, 2))
        expected = np.exp(-.04 * 2) * (100 * np.exp((.04 - .02) * 2) - 80)
        self.assertAlmostEqual(value, expected, 10)

    def test_delta_is_bounded_and_monotone_in_moneyness(self):
        strikes = np.array([50., 80., 100., 130., 200.])
        deltas = call_delta(100., strikes, .04, .02, .2, 1.)
        self.assertTrue(np.all((deltas >= 0) & (deltas <= 1)))
        self.assertTrue(np.all(np.diff(deltas) < 0))

    def test_value_rises_with_volatility(self):
        vols = np.array([.1, .2, .3, .4])
        values = black_scholes_call(100., 100., .04, .02, vols, 2.)
        self.assertTrue(np.all(np.diff(values) > 0))

    def test_negative_spot_is_rejected(self):
        with self.assertRaises(ValueError):
            black_scholes_call(-1, 100, .04, .02, .2, 1)


class InputProxyTests(unittest.TestCase):
    def setUp(self):
        self.ix = pd.bdate_range('2000-01-03', periods=800)

    def test_dividend_yield_recovers_a_known_constant(self):
        price = pd.Series(np.exp(np.arange(len(self.ix)) * .0002), index=self.ix)
        total = price.pct_change().fillna(0.) + .03 / 252
        yields = trailing_dividend_yield(price, total)
        self.assertAlmostEqual(float(yields.iloc[-1]), .03, 2)

    def test_dividend_yield_fills_rather_than_zeroes_a_broken_stretch(self):
        price = pd.Series(np.exp(np.arange(len(self.ix)) * .0002), index=self.ix)
        total = price.pct_change().fillna(0.) + .03 / 252
        total.iloc[:300] -= .002          # a fund proxy that badly undertracks
        yields = trailing_dividend_yield(price, total)
        self.assertTrue((yields > 0).all())
        self.assertAlmostEqual(float(yields.iloc[0]), float(yields.iloc[-1]), 2)

    def test_riskfree_recovers_the_annual_cash_return(self):
        cash = pd.Series(np.full(len(self.ix), .04 / 252), index=self.ix)
        self.assertAlmostEqual(float(trailing_riskfree(cash).iloc[-1]), .04, 3)

    def test_volatility_proxy_is_lagged(self):
        returns = pd.Series(np.full(len(self.ix), .001), index=self.ix)
        returns.iloc[500] = -.20
        vol = implied_volatility_proxy(returns, .0, horizon_years=1.)
        # The crash session must not price its own option; the next one may.
        self.assertAlmostEqual(float(vol.iloc[500]), float(vol.iloc[499]), 10)
        self.assertGreater(float(vol.iloc[501]), float(vol.iloc[500]))

    def test_volatility_premium_shifts_the_level(self):
        returns = pd.Series(np.random.default_rng(3).normal(0, .01, len(self.ix)), index=self.ix)
        base = implied_volatility_proxy(returns, .0)
        raised = implied_volatility_proxy(returns, .05)
        np.testing.assert_allclose(raised - base, .05, atol=1e-12)

    def test_negative_lag_is_rejected(self):
        returns = pd.Series(np.zeros(len(self.ix)), index=self.ix)
        with self.assertRaises(ValueError):
            implied_volatility_proxy(returns, .03, lag=-1)


class ExpiryCalendarTests(unittest.TestCase):
    """Two years out an index option exists only on a few listed dates."""

    def test_returns_third_fridays_of_the_named_months(self):
        dates = listed_expiries('2026-01-01', '2026-12-31', (1, 6, 12))
        self.assertEqual([str(d.date()) for d in dates[:3]],
                         ['2026-01-16', '2026-06-19', '2026-12-18'])
        self.assertTrue(all(d.dayofweek == 4 for d in dates))
        self.assertTrue(all(15 <= d.day <= 21 for d in dates))

    def test_covers_beyond_the_end_so_long_dates_can_be_bought(self):
        dates = listed_expiries('2026-01-01', '2026-06-30', (12,))
        self.assertGreaterEqual(dates[-1].year, 2027)

    def test_rejects_an_empty_month_set(self):
        with self.assertRaises(ValueError):
            listed_expiries('2026-01-01', '2026-12-31', ())

    def test_chooses_the_listing_nearest_the_target(self):
        dates = listed_expiries('2026-01-01', '2030-12-31', (1, 6, 12))
        picked = choose_expiry(pd.Timestamp('2026-09-07'), dates, 2., 1.)
        # Two years out is 2028-09; June and December 2028 straddle it.
        self.assertIn(str(picked.date()), ('2028-06-16', '2028-12-15'))
        self.assertLess(abs((picked - pd.Timestamp('2028-09-07')).days), 100)

    def test_never_returns_something_shorter_than_the_roll_horizon(self):
        dates = listed_expiries('2026-01-01', '2030-12-31', (1, 6, 12))
        picked = choose_expiry(pd.Timestamp('2026-09-07'), dates, 2., 1.5)
        self.assertGreater((picked - pd.Timestamp('2026-09-07')).days, 1.5 * 365.25)

    def test_returns_none_when_nothing_listed_is_long_enough(self):
        dates = listed_expiries('2026-01-01', '2026-12-31', (1,))
        self.assertIsNone(choose_expiry(pd.Timestamp('2026-09-07'), dates[:1], 2., 1.))


class SimulatorTests(unittest.TestCase):
    def setUp(self):
        self.closes = pd.bdate_range('2000-01-03', periods=1600)
        rng = np.random.default_rng(11)
        steps = rng.normal(.0003, .011, len(self.closes) - 1)
        self.price = pd.Series(np.r_[100., 100 * np.exp(np.cumsum(steps))], index=self.closes)
        self.safe = pd.Series(np.full(len(self.closes) - 1, .00015), index=self.closes[1:])
        self.q = pd.Series(.02, index=self.closes)
        self.r = pd.Series(.04, index=self.closes)
        self.vol = pd.Series(.18, index=self.closes)

    def simulate(self, **kwargs):
        return simulate_leaps_portfolio(self.price, self.safe, self.q, self.r, self.vol,
                                        LeapsRule(**kwargs))

    def test_wealth_starts_at_one_and_stays_positive(self):
        nav, _, _ = self.simulate(premium_budget=.4)
        self.assertEqual(float(nav.iloc[0]), 1.)
        self.assertTrue((nav > 0).all())

    def test_fixed_budget_holds_the_safe_weight_constant(self):
        _, _, rolls = self.simulate(premium_budget=.4)
        np.testing.assert_allclose(rolls.safe_weight, .6, atol=1e-12)

    def test_fixed_budget_floors_the_loss_within_one_roll(self):
        """A collapse between rolls cannot cost more than the premium spent."""
        crashing = self.price.copy()
        crashing.iloc[400:] = crashing.iloc[400] * 1e-6
        nav, _, rolls = simulate_leaps_portfolio(crashing, self.safe, self.q, self.r,
                                                 self.vol, LeapsRule(premium_budget=.4))
        opened = rolls[rolls.close <= self.closes[400]].iloc[-1]
        start = float(nav.loc[opened.close])
        self.assertGreater(float(nav.iloc[401]) / start, .59)

    def test_restriking_does_not_floor_the_loss_across_rolls(self):
        """The trap the report documents: bounded per contract, not per portfolio."""
        crashing = self.price.copy()
        crashing.iloc[300:] = crashing.iloc[300] * np.exp(
            np.linspace(0, -6, len(crashing) - 300))
        floored, _, _ = simulate_leaps_portfolio(crashing, self.safe, self.q, self.r,
                                                 self.vol, LeapsRule(premium_budget=.4))
        restruck, _, _ = simulate_leaps_portfolio(crashing, self.safe, self.q, self.r,
                                                  self.vol, LeapsRule(premium_budget=None))
        self.assertLess(float(restruck.iloc[-1]), float(floored.iloc[-1]))

    def test_a_long_call_portfolio_never_gains_on_a_crash(self):
        """Regression: a fast volatility proxy once made 1987-10-19 a 10% gain."""
        shocked = self.price.copy()
        shocked.iloc[700:] *= .80
        vol = implied_volatility_proxy(shocked.pct_change().fillna(0.), .03, horizon_years=2.)
        nav, _, _ = simulate_leaps_portfolio(shocked, self.safe, self.q, self.r, vol,
                                             LeapsRule(premium_budget=.5))
        self.assertLess(float(nav.iloc[700]) / float(nav.iloc[699]), 1.)

    def test_costs_only_reduce_wealth(self):
        free, _, _ = self.simulate(premium_budget=.4, spread_bps=0)
        charged, _, _ = self.simulate(premium_budget=.4, spread_bps=200)
        self.assertLess(float(charged.iloc[-1]), float(free.iloc[-1]))

    def test_more_budget_buys_more_exposure(self):
        _, small, _ = self.simulate(premium_budget=.3)
        _, large, _ = self.simulate(premium_budget=.6)
        self.assertLess(small.mean(), large.mean())

    def test_roll_count_follows_the_schedule_not_the_path(self):
        """Roll dates depend on the calendar alone, which is what lets it vectorize."""
        _, _, slow = self.simulate(premium_budget=.4, maturity_years=2., roll_at_years=1.)
        _, _, fast = self.simulate(premium_budget=.4, maturity_years=2., roll_at_years=1.5)
        self.assertGreater(len(fast), len(slow))
        _, _, scaled = simulate_leaps_portfolio(self.price * 3, self.safe, self.q, self.r,
                                                self.vol, LeapsRule(premium_budget=.4))
        self.assertEqual(len(scaled), len(slow))

    def test_every_position_is_bought_on_a_listed_expiry(self):
        _, _, rolls = self.simulate(premium_budget=.4, expiry_months=(1, 6, 12))
        self.assertTrue(set(rolls.expiry.dt.month) <= {1, 6, 12})
        self.assertTrue((rolls.expiry.dt.dayofweek == 4).all())

    def test_sparse_listings_make_the_maturity_bought_drift(self):
        """A single yearly listing cannot supply an exact two-year contract."""
        _, _, dense = self.simulate(premium_budget=.4, expiry_months=(1, 6, 12))
        _, _, sparse = self.simulate(premium_budget=.4, expiry_months=(12,))
        self.assertGreater(sparse.entry_years.std(), 0.)
        self.assertLessEqual(dense.entry_years.std(), sparse.entry_years.std())
        self.assertTrue((sparse.entry_years >= 1.).all())

    def test_rejects_impossible_expiry_months(self):
        for months in ((), (0,), (13,)):
            with self.assertRaises(ValueError):
                LeapsRule(premium_budget=.4, expiry_months=months)

    def test_scaling_the_index_leaves_returns_unchanged(self):
        base, _, _ = self.simulate(premium_budget=.4)
        scaled, _, _ = simulate_leaps_portfolio(self.price * 137.5, self.safe, self.q, self.r,
                                                self.vol, LeapsRule(premium_budget=.4))
        np.testing.assert_allclose(base.to_numpy(), scaled.to_numpy(), rtol=1e-10)

    def test_mismatched_calendars_are_rejected(self):
        with self.assertRaises(ValueError):
            simulate_leaps_portfolio(self.price, self.safe.iloc[1:], self.q, self.r,
                                     self.vol, LeapsRule(premium_budget=.4))

    def test_missing_inputs_are_rejected(self):
        holed = self.vol.copy()
        holed.iloc[10] = np.nan
        with self.assertRaises(ValueError):
            simulate_leaps_portfolio(self.price, self.safe, self.q, self.r, holed,
                                     LeapsRule(premium_budget=.4))

    def test_invalid_rules_are_rejected(self):
        for kwargs in (dict(moneyness=0), dict(roll_at_years=3.), dict(target_exposure=99),
                       dict(premium_budget=0.), dict(premium_budget=1.5), dict(iv_premium=5.)):
            with self.assertRaises(ValueError):
                LeapsRule(**kwargs)


class BreakEvenTests(unittest.TestCase):
    def test_solves_a_monotone_decreasing_function(self):
        premium = break_even_iv_premium(.10, lambda p: .20 - p)
        self.assertAlmostEqual(premium, .10, 3)

    def test_returns_nan_when_the_rival_is_out_of_reach(self):
        self.assertTrue(np.isnan(break_even_iv_premium(.50, lambda p: .20 - p)))
        self.assertTrue(np.isnan(break_even_iv_premium(-.50, lambda p: .20 - p)))


if __name__ == '__main__':
    unittest.main()


class RollScheduleTests(unittest.TestCase):
    """The schedule is separable from the path, which is why the bootstrap is affordable."""

    def setUp(self):
        self.closes = pd.bdate_range('2000-01-03', periods=1600)
        rng = np.random.default_rng(5)
        steps = rng.normal(.0003, .011, len(self.closes) - 1)
        self.price = pd.Series(np.r_[100., 100 * np.exp(np.cumsum(steps))], index=self.closes)
        self.safe = pd.Series(np.full(len(self.closes) - 1, .00015), index=self.closes[1:])
        self.q = pd.Series(.02, index=self.closes)
        self.r = pd.Series(.04, index=self.closes)
        self.vol = pd.Series(.18, index=self.closes)
        self.rule = LeapsRule(premium_budget=.4)

    def test_schedule_does_not_depend_on_the_price_path(self):
        other = self.price * np.linspace(1, .1, len(self.price))
        first, second = (roll_schedule(p.index, self.rule) for p in (self.price, other))
        np.testing.assert_array_equal(first.starts, second.starts)
        np.testing.assert_array_equal(first.expiry_days, second.expiry_days)

    def test_supplied_schedule_reproduces_the_default_exactly(self):
        built = simulate_leaps_portfolio(self.price, self.safe, self.q, self.r, self.vol,
                                         self.rule, ledger=False)
        passed = simulate_leaps_portfolio(self.price, self.safe, self.q, self.r, self.vol,
                                          self.rule, ledger=False,
                                          schedule=roll_schedule(self.closes, self.rule))
        np.testing.assert_array_equal(built.to_numpy(), passed.to_numpy())

    def test_schedule_for_another_rule_is_refused(self):
        other = roll_schedule(self.closes, LeapsRule(premium_budget=.4, roll_at_years=.5))
        with self.assertRaises(ValueError):
            simulate_leaps_portfolio(self.price, self.safe, self.q, self.r, self.vol,
                                     self.rule, schedule=other)

    def test_schedule_for_another_calendar_is_refused(self):
        shifted = pd.bdate_range('2001-01-03', periods=1600)
        with self.assertRaises(ValueError):
            simulate_leaps_portfolio(self.price, self.safe, self.q, self.r, self.vol,
                                     self.rule, schedule=roll_schedule(shifted, self.rule))

    def test_rolling_sooner_rolls_more_often(self):
        counts = [len(roll_schedule(self.closes, LeapsRule(premium_budget=.4,
                                                           roll_at_years=remaining)).starts)
                  for remaining in (.5, 1., 1.5)]
        self.assertEqual(counts, sorted(counts))

    def test_roll_positions_strictly_increase(self):
        for remaining in (.25, .5, 1., 1.5, 1.9):
            starts = roll_schedule(self.closes, LeapsRule(premium_budget=.4,
                                                          roll_at_years=remaining)).starts
            self.assertTrue(np.all(np.diff(starts) > 0), remaining)

    def test_array_core_matches_the_pandas_wrapper(self):
        nav, exposure, _ = simulate_leaps_portfolio(self.price, self.safe, self.q, self.r,
                                                    self.vol, self.rule)
        arrays = leaps_arrays(self.price, self.safe, self.q, self.r, self.vol)
        navs, exposures, weights, rolls = simulate_leaps_arrays(
            *arrays, roll_schedule(self.closes, self.rule))
        np.testing.assert_array_equal(navs, nav.to_numpy())
        np.testing.assert_array_equal(exposures, exposure.to_numpy())
        # Option weight and safe weight are the whole portfolio, by construction.
        self.assertTrue(np.all((weights > 0) & (weights < 1)))
        self.assertEqual(len(rolls), len(roll_schedule(self.closes, self.rule).starts))

    def test_ledger_records_the_premium_that_survived_to_the_sale(self):
        _, _, _, rolls = simulate_leaps_arrays(
            *leaps_arrays(self.price, self.safe, self.q, self.r, self.vol),
            roll_schedule(self.closes, self.rule))
        for entry in rolls:
            self.assertAlmostEqual(entry['exit_premium_ratio'],
                                   entry['exit_value'] / entry['premium'], 12)
            self.assertLessEqual(entry['trough_premium_ratio'], entry['exit_premium_ratio'])

    def test_published_ledger_keeps_its_committed_columns(self):
        _, _, frame = simulate_leaps_portfolio(self.price, self.safe, self.q, self.r,
                                               self.vol, self.rule)
        self.assertEqual(list(frame.columns), list(LEDGER_COLUMNS))
