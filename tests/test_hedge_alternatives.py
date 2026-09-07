import unittest

import numpy as np
import pandas as pd

from letf.hedge_alternatives import (_table, cagr, concentration_table, describe,
                                     max_drawdown, window_table)


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
