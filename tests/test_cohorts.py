import unittest

import numpy as np
import pandas as pd

from letf.cohorts import (cohort_cagrs, cohort_frame, cohort_quantiles,
                          month_end_mask, nav_path, worst_cagr)


class CohortTests(unittest.TestCase):
    def setUp(self):
        self.calendar = pd.bdate_range('2000-01-03', '2012-12-31')

    def flat(self, rate):
        r = pd.Series(rate, index=self.calendar[1:])
        return nav_path(r, self.calendar)

    def test_entry_close_is_the_session_before_the_first_return(self):
        r = pd.Series(.01, index=self.calendar[1:])
        nav = nav_path(r, self.calendar)
        self.assertEqual(nav.index[0], self.calendar[0])
        self.assertEqual(nav.iloc[0], 1.0)
        # The first return is earned inside the path, not used to set the entry.
        self.assertAlmostEqual(nav.iloc[1], 1.01)

    def test_month_end_mask_selects_last_observed_close_per_month(self):
        ix = pd.DatetimeIndex(['2020-01-02', '2020-01-31', '2020-02-03', '2020-02-28'])
        np.testing.assert_array_equal(month_end_mask(ix), [False, True, False, True])

    def test_exit_is_first_close_on_or_after_the_exact_anniversary(self):
        nav = self.flat(0.)
        frame = cohort_frame(nav, 10)
        entry = pd.Timestamp(frame.entry_close.iloc[0])
        exit_ = pd.Timestamp(frame.exit_close.iloc[0])
        self.assertGreaterEqual(exit_, entry + pd.DateOffset(years=10))
        earlier = self.calendar[self.calendar < exit_]
        self.assertLess(earlier[-1], entry + pd.DateOffset(years=10))

    def test_constant_growth_recovers_its_own_cagr(self):
        """Windows differ slightly in trading days per calendar day, so allow 0.1%."""
        daily = 1.10 ** (1 / 252) - 1
        values = cohort_cagrs(nav_path(pd.Series(daily, index=self.calendar[1:]), self.calendar), 10)
        self.assertTrue(len(values) > 20)
        np.testing.assert_allclose(values, values.mean(), rtol=1e-3)

    def test_terminated_sleeve_is_excluded_not_counted_as_nan(self):
        """A zero entry close cannot be bought; without the guard it poisons quantiles."""
        r = pd.Series(0., index=self.calendar[1:])
        r.iloc[500] = -1.0
        nav = nav_path(r, self.calendar)
        self.assertEqual(nav.iloc[-1], 0.0)
        values = cohort_cagrs(nav, 10)
        self.assertFalse(np.isnan(values).any())
        self.assertTrue((values == -1).all())
        self.assertLess(len(values), int(month_end_mask(nav.index).sum()))
        _, q = cohort_quantiles(nav, 10, (.5,))
        self.assertFalse(np.isnan(q).any())

    def test_no_eligible_window_returns_empty_and_nan_quantiles(self):
        nav = self.flat(0.)
        self.assertEqual(len(cohort_cagrs(nav, 50)), 0)
        self.assertTrue(np.isnan(worst_cagr(nav, 50)))
        values, q = cohort_quantiles(nav, 50, (.1, .5, .9))
        self.assertEqual(len(values), 0)
        self.assertTrue(np.isnan(q).all())

    def test_daily_entries_are_a_superset_of_month_end_entries(self):
        nav = self.flat(.0002)
        self.assertGreater(len(cohort_cagrs(nav, 10, month_end=False)),
                           len(cohort_cagrs(nav, 10, month_end=True)))
        self.assertLessEqual(worst_cagr(nav, 10), np.min(cohort_cagrs(nav, 10)))

    def test_frame_multiple_and_cagr_agree(self):
        rng = np.random.default_rng(0)
        r = pd.Series(rng.normal(.0003, .01, len(self.calendar) - 1), index=self.calendar[1:])
        frame = cohort_frame(nav_path(r, self.calendar), 10)
        elapsed = (pd.to_datetime(frame.exit_close) - pd.to_datetime(frame.entry_close)).dt.days
        np.testing.assert_allclose(frame.cagr, frame.multiple ** (365.25 / elapsed) - 1)


if __name__ == '__main__':
    unittest.main()


class NavPathGuardTests(unittest.TestCase):
    def test_restricted_window_as_calendar_is_rejected(self):
        """calendar[0-1] wraps to the last session and yields a path that ends before it starts."""
        calendar = pd.bdate_range('2000-01-03', '2005-12-30')
        r = pd.Series(.001, index=calendar)
        with self.assertRaises(ValueError):
            nav_path(r, calendar)
        # The same returns with a calendar that has room for the entry close are fine.
        wider = pd.bdate_range('1999-12-31', '2005-12-30')
        self.assertEqual(nav_path(r, wider).index[0], wider[0])
