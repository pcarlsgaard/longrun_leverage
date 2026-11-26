import unittest
from types import SimpleNamespace

import numpy as np
import pandas as pd

from letf.secular_regime_stress import (
    transform, nominal_log, SLOW, FAVORABLE, true_state, quarterly_states,
    adaptive_equity, adaptive_options, option_segment, run_path, nav, metrics,
)
from letf.leaps_robustness import (
    PRICE, EQUITY_TR, CASH_R, TREASURY_R, WARMUP_SESSIONS, YIELD,
)
from letf.leaps_frontier import NDX_PRICE, NDX_TOTAL, NDX_YIELD, POOL_WIDTH
from letf.treasury_leverage import FINANCING


def synthetic_pool(n=1800):
    rng = np.random.default_rng(123)
    a = np.zeros((n, POOL_WIDTH))
    a[:, PRICE] = rng.normal(.0003,.01,n)
    a[:, EQUITY_TR] = np.expm1(np.log1p(a[:, PRICE])+.00006)
    a[:, NDX_PRICE] = rng.normal(.0004,.014,n)
    a[:, NDX_TOTAL] = np.expm1(np.log1p(a[:, NDX_PRICE])+.00003)
    a[:, TREASURY_R] = rng.normal(.0001,.007,n)
    a[:, CASH_R] = .00012
    a[:, FINANCING] = .00014
    a[:, YIELD] = .015
    a[:, NDX_YIELD] = .008
    return a


class SecularStressTests(unittest.TestCase):
    def test_population_targets_and_relative_dividends(self):
        p = synthetic_pool()
        x = transform(p, p, SLOW)
        self.assertAlmostEqual(np.log1p(x[:, EQUITY_TR]).mean()*252,
                               nominal_log(SLOW.equity), places=11)
        self.assertAlmostEqual(np.log1p(x[:, CASH_R]).mean()*252,
                               nominal_log(SLOW.cash), places=11)
        self.assertAlmostEqual(np.log1p(x[:, TREASURY_R]).mean()*252,
                               nominal_log(SLOW.bond), places=11)
        self.assertAlmostEqual(np.log1p(x[:, PRICE]).std()*np.sqrt(252),
                               SLOW.vol, places=11)
        self.assertAlmostEqual(np.corrcoef(np.log1p(x[:, PRICE]),
                               np.log1p(x[:, TREASURY_R]))[0,1],
                               SLOW.correlation, places=11)
        np.testing.assert_allclose(np.log1p(x[:, EQUITY_TR])-np.log1p(x[:, PRICE]),
                                  np.log1p(p[:, EQUITY_TR])-np.log1p(p[:, PRICE]))
        self.assertGreater(true_state(FAVORABLE), true_state(SLOW))

    def test_no_path_recentering(self):
        p = synthetic_pool()
        x = transform(p[:100], p, SLOW)
        np.testing.assert_allclose(x, transform(p,p,SLOW)[:100])

    def test_quarterly_signal_is_causal_and_delayed(self):
        calendar = pd.bdate_range('2000-01-03',periods=1500)
        entry = WARMUP_SESSIONS
        p = synthetic_pool(len(calendar)-1)
        cut = 950
        altered = p.copy()
        altered[cut:, PRICE] = -.02
        altered[cut:, EQUITY_TR] = -.02
        altered[cut:, CASH_R] = .001
        def signal(x, confirm=2, lag=2):
            price = np.r_[1.,np.cumprod(1+x[:,PRICE])]
            return quarterly_states(price,x[:,EQUITY_TR],x[:,CASH_R],
                                    calendar,entry,confirm=confirm,lag=lag)
        np.testing.assert_array_equal(signal(p)[:cut-entry+1],
                                      signal(altered)[:cut-entry+1])
        one, two = signal(altered,1,1), signal(altered,1,2)
        np.testing.assert_array_equal(one[:-1],two[1:])
        self.assertEqual(signal(altered,2)[0],1)

    def test_switch_cost_and_absorbing_loss(self):
        state = np.array([0,2,2,0])
        result = adaptive_equity(np.array([.01,.01,.01]),np.zeros(3),
                                 np.ones(3),state,0.,cost=.01)
        np.testing.assert_allclose(result, [1,1.01,1.01*1.03*.99,
                                             1.01*1.03*.99*1.03])
        np.testing.assert_allclose(nav(np.array([-.2,-1.,.9])),[1,.8,0,0])

    def test_adaptive_call_without_switch_equals_fixed_contract(self):
        calendar = pd.bdate_range('2000-01-03',periods=650)
        n = len(calendar)
        spot = np.exp(np.arange(n)*.0003)
        q, rate, iv = (np.full(n,v) for v in (.015,.04,.22))
        cash = np.full(n-1,.00015)
        total = np.diff(spot)/spot[:-1]
        fixed = option_segment(calendar,spot,q,rate,iv,cash,'80_25',0,n-1)
        adapt = adaptive_options(calendar,spot,q,rate,iv,total,cash,
                                 np.ones(n,dtype=int))
        np.testing.assert_allclose(fixed,adapt)
        red = adaptive_options(calendar,spot,q,rate,iv,total,cash,
                                np.zeros(n,dtype=int))
        np.testing.assert_allclose(red,nav(total))
        state = np.ones(n,dtype=int)
        state[250:] = 0
        switched = adaptive_options(calendar,spot,q,rate,iv,total,cash,state)
        first = option_segment(calendar,spot,q,rate,iv,cash,'80_25',0,250,
                               liquidate=True)
        np.testing.assert_allclose(switched[:251],first)
        self.assertAlmostEqual(switched[251],first[-1]*(1+total[250])*.9975)

    def test_path_smoke(self):
        e = WARMUP_SESSIONS
        calendar = pd.bdate_range('2000-01-03', periods=e+340)
        closes = calendar[e:]
        days = (closes-closes[0]).days.to_numpy(dtype=float)
        horizon = SimpleNamespace(calendar=calendar,closes=closes,days=days,
                                  elapsed_days=days[-1])
        pool = synthetic_pool(len(calendar)-1)
        cfg = {'funds':{name:{'expense':.008}
                        for name in ('UPRO','SSO','TQQQ')}}
        truth = np.full(len(closes),true_state(SLOW),dtype=int)
        out, detection = run_path(transform(pool,pool,SLOW),horizon,cfg,truth)
        self.assertIn('NDX_ADAPT_CALL_COMBINED_Q2',out)
        self.assertIn('SPX_90_40_LONG',out)
        self.assertIn('ORACLE',detection)
        self.assertTrue(np.isfinite(list(out.values())).all())

if __name__ == '__main__':
    unittest.main()
