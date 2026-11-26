import unittest
import numpy as np
from letf.forward_regime_signal import evaluate, grid
import pandas as pd

class ForwardSignalTests(unittest.TestCase):
    def setUp(self):
        self.row=dict(decision_date='2026-09-30',published_date='2026-09-29',
            index='NDX',forward_pe=30,dividend_yield=.006,
            nominal_eps_growth=.10,cash_yield=.04,realized_vol=.23,inflation=.025)

    def test_threshold_and_multiple(self):
        a=evaluate(self.row,30)
        b=evaluate(self.row,24)
        self.assertAlmostEqual(a['assumed_log_equity_premium'],
            np.log1p(.10)+np.log1p(.006)-np.log1p(.04))
        self.assertLess(b['margin_3x'],a['margin_3x'])
        r=dict(self.row,nominal_eps_growth=a['required_eps_growth_3x'])
        self.assertAlmostEqual(evaluate(r,30)['margin_3x'],0.)
        self.assertEqual(len(grid(pd.DataFrame([self.row]))),12)

    def test_future_publication_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(dict(self.row,published_date='2026-10-01'),30)

if __name__=='__main__':
    unittest.main()
