import unittest
import numpy as np
import pandas as pd

from letf.falsification import level_position
from letf.price_signal_revision import price_return, price_volatility_position


class PriceSignalRevisionTests(unittest.TestCase):
    def test_price_return_excludes_distribution_jump(self):
        ix = pd.bdate_range('2020-01-01', periods=40)
        price = pd.Series(np.linspace(100, 120, len(ix)), index=ix)
        total_return_level = price.copy()
        total_return_level.iloc[20:] *= 1.02  # synthetic reinvested distribution
        pr = price_return(price)
        tr = total_return_level.pct_change(fill_method=None)
        self.assertNotAlmostEqual(pr.iloc[20], tr.iloc[20])
        self.assertAlmostEqual(pr.iloc[20], price.pct_change(fill_method=None).iloc[20])

    def test_sma_uses_price_levels_and_lag(self):
        ix = pd.bdate_range('2020-01-01', periods=300)
        price = pd.Series(100 + np.arange(len(ix)) * .1, index=ix)
        p1 = level_position(price, ix, 150, 1)
        p2 = level_position(price, ix, 150, 2)
        pd.testing.assert_series_equal(p2, p1.shift(1), check_names=False)
        self.assertEqual(p1.dropna().iloc[-1], 1.)

    def test_volatility_rule_uses_supplied_price_returns_only(self):
        ix = pd.bdate_range('2020-01-01', periods=80)
        low = pd.Series(.001 * np.sin(np.arange(80)), index=ix)
        high = low.copy(); high.iloc[40:] *= 30
        p_low, v_low = price_volatility_position(low, window=20, lag=1, binary=True)
        p_high, v_high = price_volatility_position(high, window=20, lag=1, binary=True)
        self.assertGreater(v_high.dropna().iloc[-1], v_low.dropna().iloc[-1])
        self.assertEqual(p_low.dropna().iloc[-1], 3.)
        self.assertEqual(p_high.dropna().iloc[-1], 1.)


if __name__ == '__main__':
    unittest.main()
