import unittest
import numpy as np
from letf.nasdaq_entry_decision import evaluate
from letf.options import black_scholes_call

class EntryDecisionTests(unittest.TestCase):
    def test_quote_and_scenario(self):
        spot,k,r,q,v,t=100.,90.,.0461,.006,.26,2.
        ask=float(black_scholes_call(spot,k,r,q,v,t))
        row=dict(name='90/40',spot=spot,strike=k,ask=ask,years=t,
                 rate=r,dividend=q,budget=.4)
        x=evaluate(row,.0434,.0524,long_weight=0.,exit_spread=0.)
        np.testing.assert_allclose(x.entry_iv,v,atol=1e-8)
        y=x[(x.equity_price_return==0)&(x.yield_shock==0)&(x.iv_shock==0)].iloc[0]
        mark=float(black_scholes_call(spot,k,r,q,v,1.))
        self.assertAlmostEqual(y.portfolio_return,.4*mark/ask+.6*1.0434-1)
        self.assertGreater(y.entry_delta_exposure,0)
        self.assertEqual(len(x),135)
        z=evaluate(row,.0434,.0524,long_weight=1.)
        a=z[(z.equity_price_return==0)&(z.iv_shock==0)]
        self.assertLess(a.loc[a.yield_shock==.01,'reserve_return'].iloc[0],
                        a.loc[a.yield_shock==0,'reserve_return'].iloc[0])
    def test_matched_initial_delta(self):
        spot,r,q,v,t=100.,.0461,.006,.26,2.
        budgets=[]
        for k in (80.,85.,90.):
            ask=float(black_scholes_call(spot,k,r,q,v,t))
            row=dict(name=str(k),spot=spot,strike=k,ask=ask,years=t,
                     rate=r,dividend=q,budget=.3)
            x=evaluate(row,.0434,.0524,target_delta=1.)
            np.testing.assert_allclose(x.entry_delta_exposure,1.)
            budgets.append(x.budget.iloc[0])
        self.assertGreater(budgets[0],budgets[-1])
        with self.assertRaises(ValueError):
            evaluate(row,.0434,.0524,target_delta=20.)

if __name__=='__main__':
    unittest.main()
