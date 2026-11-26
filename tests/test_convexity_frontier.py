import unittest
import numpy as np
import pandas as pd
from letf.options import roll_schedule, simulate_leaps_arrays
from letf.leaps_robustness import leaps_rule
from letf.convexity_frontier import flat_marks, call_marks, pair_marks, roll_portfolio

class ConvexityTests(unittest.TestCase):
    def setUp(self):
        self.cal=pd.bdate_range('2000-01-03','2004-01-02')
        self.days=(self.cal-self.cal[0]).days.to_numpy(dtype=float)
        self.s=100*np.exp(.07*self.days/365.25)
        self.q=np.full(len(self.s),.006)
        self.r=np.full(len(self.s),.04)
        self.iv=np.full(len(self.s),.29)
        self.safe=np.full(len(self.s)-1,.00015)
        self.sched=roll_schedule(self.cal,leaps_rule(.90,.40,1.))
        self.geo=flat_marks(self.s,self.q,self.r,self.iv,self.days,self.sched)
        self.long=call_marks(self.s,self.q,self.r,self.iv,self.geo,.90)
        self.short=call_marks(self.s,self.q,self.r,self.iv,self.geo,1.35)

    def test_outright_parity(self):
        ref=simulate_leaps_arrays(self.s,np.r_[1.,1+self.safe],self.q,self.r,
                    self.iv,self.days,self.sched,ledger=False)
        w,d,c=roll_portfolio(self.s,self.safe,self.geo,self.long,
                              self.short[:2],0.,.4)
        np.testing.assert_allclose(w,ref.navs,rtol=1e-12)
        self.assertEqual(c,0.)

    def test_bounds_and_spreads(self):
        sp,sd,bound=pair_marks(self.long,self.short,self.s,self.q,self.r,self.geo,.20)
        _,_,_,_,flat,t=self.geo
        width=(self.short[2]-self.long[2])*np.exp(-self.r[flat]*t)
        self.assertTrue(np.all(self.long[0]-sp>=-1e-10))
        self.assertTrue(np.all(self.long[0]-sp<=width+1e-10))
        self.assertTrue(np.all(sp<=self.short[0]+1e-10))
        a,_,_=roll_portfolio(self.s,self.safe,self.geo,self.long,(sp,sd),.5,.4,spread=0.)
        b,_,_=roll_portfolio(self.s,self.safe,self.geo,self.long,(sp,sd),.5,.4,spread=.01)
        self.assertLess(b[-1],a[-1])

    def test_delta_budget_cap(self):
        w,d,c=roll_portfolio(self.s,self.safe,self.geo,self.long,
                           self.short[:2],1.,.5,10.)
        self.assertEqual(c,1.)
        self.assertLess(d,10.)
        self.assertTrue(np.all(w>0))
if __name__=='__main__':
    unittest.main()
