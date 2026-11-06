"""Targeted tests for SMA whipsaw policies and no-lookahead execution."""
import unittest

import numpy as np
import pandas as pd

from letf.signals import level_position
from letf.sma_whipsaw import (
    brief_breaches, event_rows, executed_states, raw_states, whipsaws,
)
from letf.strategy import switching_costs, transitions


class RawPolicyTests(unittest.TestCase):
    def setUp(self):
        self.ix = pd.bdate_range("2024-01-02", periods=13)

    def test_baseline_exactly_reproduces_canonical_price_only_sma(self):
        price = pd.Series([10, 11, 12, 9, 9, 13, 11, 13, 14, 10, 15, 16, 17],
                          index=self.ix)
        raw = raw_states(price, self.ix, 3, "BASE")
        for lag in (1, 2, 3):
            pd.testing.assert_series_equal(
                executed_states(raw, lag),
                level_position(price, self.ix, 3, lag=1).shift(lag-1),
                check_names=False)
        self.assertTrue(raw.iloc[:2].isna().all())
        self.assertEqual(raw.iloc[3], 0)
        self.assertEqual(raw.iloc[5], 1)

    def test_two_close_bear_confirmation_prevents_single_day_exit(self):
        price = pd.Series([10, 11, 12, 9, 9, 13, 11, 13, 14, 10, 15, 16, 17],
                          index=self.ix)
        base = raw_states(price, self.ix, 3, "BASE")
        confirm = raw_states(price, self.ix, 3, "CONFIRM_BEAR2")
        self.assertEqual(base.iloc[3], 0)
        self.assertEqual(confirm.iloc[3], 1)
        self.assertEqual(confirm.iloc[4], 0)
        self.assertEqual(confirm.iloc[5], 1)
        self.assertEqual(base.iloc[6], 0)
        self.assertEqual(confirm.iloc[6], 1)

    def test_exit_buffer_requires_full_half_percent_breach(self):
        values = [10.0, 10.1, 10.2, 10.09, 10.24, 9.0, 9.0,
                  10.9, 11.0, 11.0, 11.1, 11.2, 11.3]
        price = pd.Series(values, index=self.ix)
        base = raw_states(price, self.ix, 3, "BASE")
        buffer = raw_states(price, self.ix, 3, "BUFFER_EXIT_50BP")
        self.assertEqual(base.iloc[3], 0)  # 10.09 < SMA ~= 10.13
        self.assertEqual(buffer.iloc[3], 1)  # but not 0.5% under
        self.assertEqual(buffer.iloc[5], 0)  # deep drop does trigger exit
        self.assertEqual(buffer.iloc[7], 1)  # re-enter above SMA

    def test_reject_bad_rules_and_bad_price_history(self):
        price = pd.Series(np.arange(1., 14.), index=self.ix)
        with self.assertRaises(ValueError):
            raw_states(price, self.ix, 3, "SEARCH_BEST")
        with self.assertRaises(ValueError):
            raw_states(price.drop(self.ix[7]), self.ix, 3, "BASE")
        with self.assertRaises(ValueError):
            raw_states(price, self.ix, 1, "BASE")

    def test_raw_bear_break_lengths_counted_in_close_sessions(self):
        raw = pd.Series([np.nan, 1., 1, 0, 1, 1, 0, 0, 1, 1,
                         0, 0, 0], dtype=float)
        self.assertEqual(brief_breaches(raw, 1), 1)
        self.assertEqual(brief_breaches(raw, 2), 2)
        self.assertEqual(brief_breaches(raw, 10), 2)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.ix = pd.bdate_range("2024-01-02", periods=13)
        self.raw = pd.Series([np.nan, 1, 1, 0, 1, 1, 0, 0, 1, 1, 1, 0, 1],
                             index=self.ix, dtype=float)

    def test_cancel_unfilled_sell_on_next_close_recovery(self):
        plain = executed_states(self.raw, 2)
        cancel = executed_states(self.raw, 2, cancel_pending_sell=True)
        # Sell signalled at close #3, reversal at close #4; return #5 is unhedged.
        self.assertEqual(plain.iloc[5], 0)
        self.assertEqual(cancel.iloc[5], 1)
        # Persistent bearish closes #6/7 must eventually execute a sell.
        self.assertEqual(cancel.iloc[8], 0)
        # Never retroactively cancel a sale after it has executed.
        self.assertEqual(cancel.iloc[9], 0)
        self.assertEqual(cancel.iloc[10], 1)

    def test_two_extra_sessions_cancel_on_any_intermediate_recovery(self):
        p = executed_states(self.raw, 3, cancel_pending_sell=True)
        self.assertEqual(p.iloc[6], 1)  # signal #3 canceled by close #4
        self.assertEqual(p.iloc[9], 1)  # signal #6 has recovery close #8
        with self.assertRaises(ValueError):
            executed_states(self.raw, 1, cancel_pending_sell=True)
        with self.assertRaises(ValueError):
            executed_states(self.raw, 4)

    def test_future_prices_cannot_change_any_pre_execution_return(self):
        for lag in (1, 2, 3):
            for cancel in (False, True):
                if lag == 1 and cancel:
                    continue
                left = executed_states(self.raw, lag, cancel)
                changed = self.raw.copy()
                changed.iloc[7:] = 1.0
                right = executed_states(changed, lag, cancel)
                # At return-end #7 the close #7 is not yet observable.
                pd.testing.assert_series_equal(left.iloc[:8], right.iloc[:8])

    def test_one_switch_cost_per_direction_and_realized_whipsaw(self):
        p = pd.Series([1., 1, 0, 1, 1, 0, 0, 1, 1, 1, 1, 1, 1],
                      index=self.ix)
        gross = pd.Series(0., index=self.ix)
        net = switching_costs(gross, p, 25)
        self.assertEqual(int(transitions(p).sum()), 4)
        self.assertAlmostEqual(float((1+net).prod()), 0.9975**4)
        self.assertEqual(whipsaws(p, 2), 3)
        self.assertEqual(whipsaws(p, 10), 3)


class EventAccountingTests(unittest.TestCase):
    def test_extra_leverage_risk_reported_without_lookahead(self):
        ix = pd.bdate_range("1987-01-02", periods=8)
        base_r = pd.Series([.01, -.02, -.01, .01, .01, .01, 0., 0.], index=ix)
        variant_r = pd.Series([.01, -.06, -.03, .01, .01, .01, 0., 0.], index=ix)
        base_p = pd.Series([1, 0, 0, 0, 1, 1, 1, 1], index=ix)
        variant_p = pd.Series([1, 1, 1, 0, 1, 1, 1, 1], index=ix)
        out = event_rows(variant_r, variant_p, base_r, base_p, ix)
        row = next(x for x in out if x["event"] == "1987")
        self.assertEqual(row["extra_3x_days_vs_base"], 2)
        self.assertLess(row["cumulative_log_advantage_vs_base"], 0)
        self.assertLess(row["adverse_extra_3x_log_gap"], 0)
        self.assertEqual(row["first_executed_1x_return_end"], str(ix[3].date()))


if __name__ == "__main__":
    unittest.main()
