import unittest

import numpy as np
import pandas as pd

from letf.hedge_alternatives import (_table, cagr, concentration_table, constant_leverage,
                                     describe, implied_financing, max_drawdown,
                                     window_table)


def constant(rate, index):
    return pd.Series((1 + rate) ** (1 / 252) - 1, index=index)


class MetricTests(unittest.TestCase):
    def setUp(self):
        # Long enough for both cohort horizons the report reads to exist.
        self.ix = pd.bdate_range('1990-01-02', periods=252 * 32)

    def test_cagr_recovers_a_constant_growth_rate(self):
        # A business-day calendar runs slightly over 252 sessions a year, so the
        # recovered rate is close to but not exactly the per-session input.
        self.assertAlmostEqual(cagr(constant(.10, self.ix)), .10, 1)

    def test_max_drawdown_is_zero_for_a_monotone_path(self):
        self.assertEqual(max_drawdown(constant(.10, self.ix)), 0.)

    def test_max_drawdown_finds_a_known_trough(self):
        returns = pd.Series(0., index=self.ix)
        returns.iloc[100] = -.5
        returns.iloc[200] = 1.
        self.assertAlmostEqual(max_drawdown(returns), -.5, 10)

    def test_describe_reports_every_column_the_report_reads(self):
        # nav_path needs an entry close, so the calendar leads the return series.
        calendar = pd.bdate_range(self.ix[0] - pd.Timedelta(days=7), self.ix[-1])
        row = describe('X', constant(.10, self.ix), calendar, exposure=2.)
        for key in ('series', 'cagr', 'max_drawdown', 'annualized_volatility',
                    'mean_delta_exposure', 'cohort_20y_min_cagr', 'cohort_30y_min_cagr'):
            self.assertIn(key, row)
        self.assertEqual(row['mean_delta_exposure'], 2.)


class TableTests(unittest.TestCase):
    def setUp(self):
        self.ix = pd.bdate_range('2000-01-03', periods=252 * 12)
        rng = np.random.default_rng(5)
        self.candidates = {
            'FAST': (pd.Series(rng.normal(.0006, .01, len(self.ix)), index=self.ix), 2.),
            'SLOW': (pd.Series(rng.normal(.0002, .005, len(self.ix)), index=self.ix), 1.),
        }

    def test_concentration_skips_the_self_comparison(self):
        table = concentration_table(self.candidates, ('SLOW',))
        self.assertEqual(list(table.series), ['FAST'])
        self.assertEqual(list(table.benchmark), ['SLOW'])

    def test_concentration_reports_every_benchmark(self):
        table = concentration_table(self.candidates, ('SLOW', 'FAST'))
        self.assertEqual(len(table), 2)
        self.assertEqual(set(table.benchmark), {'SLOW', 'FAST'})

    def test_excluded_top_days_reconstruct_the_residual_advantage(self):
        table = concentration_table(self.candidates, ('SLOW',)).iloc[0]
        expected = table.total_log_advantage * (1 - table.top20_day_share)
        self.assertAlmostEqual(table.advantage_excluding_top_20_days, expected, 12)
        self.assertAlmostEqual(table.wealth_ratio_excluding_top_20_days,
                               float(np.exp(expected)), 12)

    def test_window_table_annualizes_only_when_asked(self):
        windows = {'first_half': ('2000-01-03', '2005-12-31')}
        annual = window_table(self.candidates, windows, annualize=True).set_index('series')
        total = window_table(self.candidates, windows, annualize=False).set_index('series')
        piece = self.candidates['FAST'][0].loc['2000-01-03':'2005-12-31']
        self.assertAlmostEqual(total.loc['FAST', 'first_half'],
                               float((1 + piece).prod() - 1), 10)
        self.assertNotAlmostEqual(annual.loc['FAST', 'first_half'],
                                  total.loc['FAST', 'first_half'], 3)


class MarkdownTests(unittest.TestCase):
    def test_formats_by_column_and_keeps_alignment(self):
        frame = pd.DataFrame([{'name': 'a', 'share': .1234}, {'name': 'b', 'share': np.nan}])
        out = _table(frame, ['name', 'share'], {'share': '.1%'})
        lines = out.splitlines()
        self.assertEqual(lines[0], '| name | share |')
        self.assertEqual(lines[1], '|---|---:|')
        self.assertEqual(lines[2], '| a | 12.3% |')
        self.assertEqual(lines[3], '| b |  |')


if __name__ == '__main__':
    unittest.main()


class ResetLadderTests(unittest.TestCase):
    def setUp(self):
        self.ix = pd.bdate_range('2000-01-03', periods=1500)
        rng = np.random.default_rng(17)
        self.u = pd.Series(rng.normal(.0004, .011, len(self.ix)), index=self.ix)
        self.f = pd.Series(.04 / 252, index=self.ix)

    def test_unit_leverage_is_the_underlying(self):
        out = constant_leverage(self.u, self.f, 1., 'M')
        np.testing.assert_allclose(out.to_numpy(), self.u.to_numpy(), atol=1e-14)

    def test_daily_reset_matches_the_fund_identity(self):
        out = constant_leverage(self.u, self.f, 3., 'D')
        np.testing.assert_allclose(out.to_numpy(), (3 * self.u - 2 * self.f).to_numpy(), atol=1e-14)

    def grind(self):
        """A slide no single session could bust, but a whole year of it can."""
        crashed = self.u.copy()
        crashed.iloc[500:530] = -.05
        return crashed

    def test_wipeout_is_absorbing(self):
        wealth = (1 + constant_leverage(self.grind(), self.f, 3., 'Y')).cumprod()
        self.assertEqual(float(wealth.iloc[-1]), 0.)
        dead = wealth[wealth <= 0].index[0]
        self.assertTrue((wealth.loc[dead:] == 0).all())

    def test_a_daily_reset_survives_what_a_slow_reset_does_not(self):
        """The point of the section: the cliff is reset frequency, not the drop."""
        crashed = self.grind()
        fast = (1 + constant_leverage(crashed, self.f, 3., 'D')).cumprod()
        slow = (1 + constant_leverage(crashed, self.f, 3., 'Y')).cumprod()
        self.assertGreater(float(fast.iloc[-1]), 0.)
        self.assertEqual(float(slow.iloc[-1]), 0.)

    def test_slow_reset_beats_daily_on_an_oscillating_path(self):
        """The mechanism itself: a daily reset sells low and buys high, a slow one does not."""
        swinging = pd.Series(np.where(np.arange(len(self.ix)) % 2 == 0, .02, -.02),
                             index=self.ix)
        fast = float((1 + constant_leverage(swinging, self.f, 3., 'D')).prod())
        slow = float((1 + constant_leverage(swinging, self.f, 3., 'M')).prod())
        self.assertLess(fast, slow)

    def test_slow_reset_loses_to_daily_on_a_smooth_rise(self):
        """And the benefit really is about volatility, not about patience.

        With no volatility to harvest, holding the position lets a gain dilute
        the leverage — the assets grow while the loan does not — so the slow
        reset drifts below target and earns less. Anyone reading the ladder as
        "rebalance less often" rather than "volatility is what a daily reset
        pays for" would get this backwards.
        """
        rising = pd.Series(.0006, index=self.ix)
        fast = float((1 + constant_leverage(rising, self.f, 3., 'D')).prod())
        slow = float((1 + constant_leverage(rising, self.f, 3., 'M')).prod())
        self.assertGreater(fast, slow)

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            constant_leverage(self.u, self.f.iloc[1:], 3., 'M')
        with self.assertRaises(ValueError):
            constant_leverage(self.u, self.f, .5, 'M')


class ImpliedFinancingTests(unittest.TestCase):
    """The recovered financing must be the real funding term, not a curve fit."""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        from letf.analysis import load_inputs
        cls.daily, cls.config = load_inputs(Path(__file__).resolve().parent.parent, offline=True)

    def financing(self):
        from letf.model import calendar_days
        frame = self.daily[['SP500_1X', 'UPRO_SPREAD_50BP', 'SSO_SPREAD_50BP']].dropna()
        ix = frame.index
        days = calendar_days(ix, self.daily.index[self.daily.index.get_loc(ix[0]) - 1])
        expense = self.config['funds']['UPRO']['expense']
        return frame, days, expense, implied_financing(
            frame.SP500_1X, frame.UPRO_SPREAD_50BP, 3., expense, days)

    def test_round_trips_the_series_it_was_recovered_from(self):
        frame, days, expense, financing = self.financing()
        rebuilt = 3 * frame.SP500_1X - 2 * financing - expense * days / 365
        np.testing.assert_allclose(rebuilt.to_numpy(), frame.UPRO_SPREAD_50BP.to_numpy(),
                                   atol=1e-15)

    def test_reproduces_a_fund_it_was_not_recovered_from(self):
        """Recovered from the 3x fund, it must also rebuild the 2x one."""
        frame, days, _, financing = self.financing()
        expense = self.config['funds']['SSO']['expense']
        rebuilt = 2 * frame.SP500_1X - 1 * financing - expense * days / 365
        np.testing.assert_allclose(rebuilt.to_numpy(), frame.SSO_SPREAD_50BP.to_numpy(),
                                   atol=1e-10)

    def test_recovers_a_plausible_borrowing_rate(self):
        _, days, _, financing = self.financing()
        annual = float((financing / days * 365).mean())
        self.assertTrue(.01 < annual < .08, annual)
