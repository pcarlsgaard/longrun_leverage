import unittest

import numpy as np
import pandas as pd

from letf.analysis import sma_position, cohort_summary
from letf.falsification import (level_position, transitions, switching_costs,
    volatility_position, discrete_exposure, select_returns, rolling_stats, subperiod_index,
    economic_components, attribution, stress_detail)
from letf.model import calendar_days, simulate


class FalsificationTests(unittest.TestCase):
    def setUp(self):
        self.ix = pd.bdate_range('2020-01-01', periods=12)

    def test_lag1_lag2_both_directions_and_no_leakage(self):
        levels = pd.Series([1,2,3,1,1,4,5,1,2,3,4,5],index=self.ix)
        p = level_position(levels,self.ix,3,1)
        q = level_position(levels,self.ix,3,2)
        pd.testing.assert_series_equal(q,p.shift(1))
        self.assertEqual(p.iloc[4],0)
        self.assertEqual(q.iloc[4],1)
        self.assertEqual(p.iloc[6],1)
        self.assertEqual(q.iloc[6],0)
        changed = levels.copy(); changed.iloc[5:] = 1000
        for lag in (1,2):
            a=level_position(levels,self.ix,3,lag)
            b=level_position(changed,self.ix,3,lag)
            pd.testing.assert_series_equal(a.iloc[:5+lag],b.iloc[:5+lag])

    def test_switch_cost_only_on_transitions_in_both_directions(self):
        ix=self.ix[:6]
        p=pd.Series([1,1,0,0,1,1],index=ix)
        r=pd.Series([.1,-.1,.02,0,.05,-.02],index=ix)
        cost=switching_costs(r,p,50)
        expected=(1+r).copy(); expected.iloc[[2,4]]*=.995
        np.testing.assert_allclose(cost,expected-1)
        self.assertAlmostEqual((1+cost).prod()/(1+r).prod(),.995**2)
        self.assertEqual(transitions(p).sum(),2)
        np.testing.assert_allclose(switching_costs(r,p,0),r)
        np.testing.assert_allclose(switching_costs(r,p*0,50),r)
        with self.assertRaises(ValueError): switching_costs(r,p,-1)

    def test_volatility_estimate_is_prior_only_and_additional_lag(self):
        r=pd.Series([.01,-.01]*6,index=self.ix)
        p,v=volatility_position(r,window=3)
        self.assertTrue(v.iloc[:3].isna().all())
        self.assertAlmostEqual(v.iloc[3],r.iloc[:3].std()*np.sqrt(252))
        altered=r.copy(); altered.iloc[5]=10
        q,w=volatility_position(altered,window=3)
        pd.testing.assert_series_equal(v.iloc[:6],w.iloc[:6])
        pd.testing.assert_series_equal(p.iloc[:6],q.iloc[:6])
        _,v2=volatility_position(r,window=3,lag=2)
        pd.testing.assert_series_equal(v2,v.shift(1))

    def test_exact_discrete_boundaries(self):
        desired=pd.Series([1.,1.4999,1.5,2.4999,2.5,3.,np.nan])
        np.testing.assert_allclose(discrete_exposure(desired),[1,1,2,2,3,3,np.nan])

    def test_discrete_volatility_assignment_and_zero_volatility(self):
        # Alternating +/- a has known sample SD; prescribe vol on either side
        # of target exposure thresholds without parameter search.
        for vol,state in [(.30,1),(.14,1),(.13,2),(.09,2),(.079,3),(.04,3),(0,3)]:
            amplitude=vol/np.sqrt(252)/np.sqrt(20/19)
            r=pd.Series([amplitude,-amplitude]*11,index=pd.bdate_range('2020-01-01',periods=22))
            p,_=volatility_position(r)
            self.assertEqual(p.iloc[-1],state)
        for vol,state in [(.199,3),(.201,1)]:
            a=vol/np.sqrt(252)/np.sqrt(20/19)
            r=pd.Series([a,-a]*11)
            p,_=volatility_position(r,binary=True)
            self.assertEqual(p.iloc[-1],state)

    def test_price_total_return_alignment_and_missing_session(self):
        levels=pd.Series([1,2,3,1,1,4,5,1,2,3,4,5],index=self.ix)
        r=levels.pct_change().iloc[1:]
        pd.testing.assert_series_equal(level_position(levels,self.ix,3),sma_position(r,self.ix,3))
        with self.assertRaises(ValueError): level_position(levels.drop(self.ix[4]),self.ix,3)
        p=level_position(levels,self.ix,3).dropna()
        legs=pd.DataFrame({'stock':.01,'leveraged':.03},index=self.ix)
        result=select_returns(legs,p,{0:'stock',1:'leveraged'})
        np.testing.assert_allclose(result,np.where(p.eq(1),.03,.01))

    def test_subperiod_includes_first_return_not_prior_return(self):
        ix=pd.to_datetime(['1999-12-30','1999-12-31','2000-01-03','2000-01-04','2000-12-29','2001-01-02'])
        selected=subperiod_index(ix,'2000-01-01','2000-12-31')
        self.assertTrue(selected.equals(ix[2:5]))
        self.assertEqual(ix[ix.get_loc(selected[0])-1],pd.Timestamp('1999-12-31'))
        self.assertTrue(subperiod_index(ix,'2001-01-01',None).equals(ix[-1:]))

    def test_exact_attribution_identity_and_retained_equity(self):
        r=pd.Series([.01,-.05,.03,-.02,.005,.04,-.1,.01,.02,.03,.001],index=self.ix[1:])
        days=calendar_days(r.index,self.ix[0])
        cash=pd.Series(.0001,index=r.index)
        p=pd.Series([1,0,0,1,0,1,0,1,0,1,1],index=r.index)
        for leverage in (2,3):
            lev=simulate(r,days,.04*days/360,leverage,.009,.005)
            c=economic_components(r,lev,days,leverage,.009)
            np.testing.assert_allclose(c.leveraged_log,leverage*c.underlying_log-c.path_drag_log-c.financing_drag_log-c.expense_drag_log,atol=1e-14)
            for kind,off in [('1x',r),('tbill',cash)]:
                rows=attribution(c,p,cash,leverage,kind)
                expected=(np.log1p(lev.where(p.eq(1),off))-np.log1p(lev)).sum()
                self.assertAlmostEqual(sum(row['explained_log_advantage'] for row in rows),expected)
                for row in rows: self.assertAlmostEqual(row['identity_residual'],0)
                below=rows[1]
                self.assertAlmostEqual(below['net_equity_exposure_change_log'],below['avoided_negative_equity_log']+below['forgone_positive_equity_log'])

    def test_rolling_summary_matches_existing_cohort_framework(self):
        ix=pd.bdate_range('1990-01-01','2024-01-01')
        days=calendar_days(ix[1:],ix[0])
        r=(1.1**(days/365.25)-1).rename('r')
        stats=rolling_stats(r,ix)
        original,_=cohort_summary(r.to_frame(),ix)
        for y in (20,30):
            row=original[original.horizon_years==y].iloc[0]
            self.assertAlmostEqual(stats[f'cohort_{y}y_min_cagr'],row.min_cagr)
            self.assertAlmostEqual(stats[f'cohort_{y}y_median_cagr'],row.median_cagr)
            self.assertEqual(stats[f'cohort_{y}y_count'],row.cohorts)

    def test_stress_signal_execution_and_pre_signal_loss(self):
        raw=pd.Series([1,1,1,0,0,1,1,1,1,1,1,1],index=self.ix)
        r=pd.Series([.1,.1,-.2,-.1,.1,.1,.1,.1,.1,.1,.1],index=self.ix[1:])
        for lag in (1,2):
            p=raw.shift(lag).loc[r.index].fillna(1)
            row=stress_detail(r,p,raw,self.ix,str(self.ix[1].date()),str(self.ix[8].date()),lag)
            self.assertEqual(row['first_below_sma_signal_close'],str(self.ix[3].date()))
            self.assertEqual(row['first_deleveraging_execution_close'],str(self.ix[3+lag-1].date()))
            self.assertEqual(row['first_deleveraged_return_end'],str(self.ix[3+lag].date()))
            self.assertEqual(row['leverage_resumed_execution_close'],str(self.ix[5+lag-1].date()))
            self.assertAlmostEqual(row['decline_before_signal'],.2)
            self.assertAlmostEqual(row['value_at_first_signal'],1.1*1.1*.8)


if __name__=='__main__': unittest.main()
