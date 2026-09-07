import unittest
import numpy as np
import pandas as pd
from letf.reserve import (ReserveRule, simulate_reserve, transition_turnover,
                          full_cycle_accounting, tagged_deployment_value)
from letf.falsification import switching_costs

class ReserveTests(unittest.TestCase):
    def run_rule(self, returns, rule, states=None, dd=None, lag=1, cost=0, cash=None):
        cal=pd.bdate_range('2000-01-03',periods=len(returns)+1)
        ix=cal[1:]
        def s(x): return pd.Series(x,index=ix,dtype=float)
        return simulate_reserve(s(returns),s(cash if cash is not None else np.zeros(len(ix))),
            s(states if states is not None else np.ones(len(ix))),
            s(dd if dd is not None else np.zeros(len(ix))),cal,rule,lag=lag,cost_bps=cost)

    def test_hwm_new_gain_and_no_repeated_harvest(self):
        a,_=self.run_rule([.1,0,-.1,1/9,0,0],ReserveRule('hwm',deployment='hold'))
        self.assertAlmostEqual(a.accumulation.iloc[1],.01)
        self.assertAlmostEqual(a.accumulation.sum(),.01)
        self.assertAlmostEqual(a.hwm_observed.iloc[-1],1.1)

    def test_cap_is_purchase_limit_and_hwm_advances_at_cap(self):
        a,_=self.run_rule([1,1,0,0],ReserveRule('hwm',deployment='hold',cap=.05,harvest=1))
        self.assertAlmostEqual(a.start_reserve_weight.iloc[1],.05)
        self.assertAlmostEqual(a.start_reserve_weight.iloc[2],.05)
        self.assertAlmostEqual(a.accumulation.iloc[-1],0)
        self.assertAlmostEqual(a.hwm_observed.iloc[-1],3.9)

    def test_band_drift_and_replenishment_to_ten(self):
        a,_=self.run_rule([.1,2,0],ReserveRule('band',deployment='hold',initial=.1,cap=.2))
        self.assertEqual(a.accumulation.iloc[1],0)
        self.assertAlmostEqual(a.start_reserve_weight.iloc[2],.1)

    def test_three_equal_bill_unit_tranches_including_interest(self):
        a,t=self.run_rule([0]*63,ReserveRule('hwm',initial=.15,harvest=0),
                          states=[0]+[1]*62,cash=[.001]*63)
        deployed=t[t.reason=='deployment']
        self.assertEqual(deployed.row.tolist(),[1,21,61])
        units=deployed.net_deployed.to_numpy()/1.001**deployed.row.to_numpy()
        np.testing.assert_allclose(units,.05)
        self.assertAlmostEqual(a.reserve_wealth.iloc[-1],0)

    def test_staging_resets_after_adverse_signal(self):
        states=[0]+[1]*12+[0]*4+[1]*62
        _,t=self.run_rule([0]*len(states),ReserveRule('hwm',initial=.15,harvest=0),states)
        d=t[t.reason=='deployment']
        self.assertEqual(d.row.tolist(),[1,17,37,77])
        np.testing.assert_allclose(d.net_deployed,[.05,.1/3,.1/3,.1/3])

    def test_drawdown_units_one_time_thresholds_and_gap(self):
        dd=[0,-.2,-.2,-.4,-.3,-.5,-.5]
        a,t=self.run_rule([0]*7,ReserveRule('hwm',deployment='drawdown',initial=.1,harvest=0),
                          states=[0]*7,dd=dd)
        np.testing.assert_allclose(a.deployment,[0,.02,0,.05,0,.03,0])
        self.assertTrue((t.asset=='SP500_1X').all())
        self.assertTrue((a.sleeve_leverage==1).all())
        self.assertAlmostEqual(a.reserve_wealth.iloc[-1],0)

    def test_drawdown_tranches_reset_only_after_underlying_new_high(self):
        a,_=self.run_rule([0]*5,ReserveRule('hwm',deployment='drawdown',initial=.1,harvest=0),
                         states=[0]*5,dd=[0,-.2,-.2,0,-.2])
        np.testing.assert_allclose(a.deployment,[0,.02,0,0,.016])

    def test_band_no_immediate_rebuild_after_deploy(self):
        states=[0]+[1]*64
        a,_=self.run_rule([0]*65,ReserveRule('band',initial=.1,cap=.2),states)
        self.assertEqual(a.accumulation.sum(),0)
        self.assertAlmostEqual(a.reserve_wealth.iloc[-1],0)
        returns=[0]*63+[.1,0]
        b,_=self.run_rule(returns,ReserveRule('band',initial=.1,cap=.2),states)
        self.assertAlmostEqual(b.start_reserve_weight.iloc[-1],.1)

    def test_hwm_rebuild_requires_new_total_wealth(self):
        a,_=self.run_rule([-.2]+[0]*64,ReserveRule('hwm',initial=.15),[0]+[1]*64)
        self.assertEqual(a.accumulation.sum(),0)

    def test_transfers_conserve_and_never_double_count(self):
        a,_=self.run_rule([0]*64,ReserveRule('band',initial=.1,cap=.2),[0]+[1]*63)
        np.testing.assert_allclose(a.wealth,1)
        np.testing.assert_allclose(a.risky_wealth+a.reserve_wealth,a.wealth)
        np.testing.assert_allclose(a.return_net,0)

    def test_net_transaction_cost_union_not_sum(self):
        self.assertAlmostEqual(transition_turnover(.9,.1,-.1/3,True),.9+.1/3)
        self.assertAlmostEqual(transition_turnover(.9,.1,.05,True),.9)
        a,_=self.run_rule([0,0],ReserveRule('band',initial=.1,cap=.2),[0,1],cost=50)
        self.assertAlmostEqual(a.wealth.iloc[-1],1-(.9+.1/3)*.005)
        self.assertAlmostEqual(a.transaction_cost.sum(),1-a.wealth.iloc[-1])

    def test_no_reserve_matches_falsification_cost_convention(self):
        r=[.1,-.2,.05,0]; p=[1,0,0,1]
        a,_=self.run_rule(r,ReserveRule(),p,cost=25)
        np.testing.assert_allclose(a.return_net,switching_costs(pd.Series(r),pd.Series(p),25))

    def test_accumulation_lags_and_future_return_invariance(self):
        for lag in (1,2):
            a,_=self.run_rule([.1,0,0,0],ReserveRule('hwm'),lag=lag)
            self.assertAlmostEqual(a.accumulation.iloc[lag],.01)
            b,_=self.run_rule([.1,0,.5,0],ReserveRule('hwm'),lag=lag)
            np.testing.assert_allclose(a.transfer_to_reserve.iloc[:2+lag],b.transfer_to_reserve.iloc[:2+lag])

    def test_full_cycle_cost_carried_to_same_date(self):
        a=full_cycle_accounting(100,90,200,210)
        self.assertEqual(a['pre_drawdown_opportunity_cost'],10)
        self.assertEqual(a['carried_opportunity_cost'],20)
        self.assertEqual(a['incremental_post_drawdown_wealth'],30)
        self.assertEqual(a['reserve_payback_ratio'],1.5)
        self.assertEqual(a['attribution_residual'],0)
        self.assertEqual(a['recovery_multiplier'],1.05)
        self.assertTrue(np.isnan(full_cycle_accounting(100,110,200,210)['reserve_payback_ratio']))

    def test_deployed_lot_marking(self):
        a,t=self.run_rule([0,.1,.1],ReserveRule('hwm',initial=.15,harvest=0),[0,1,1])
        d=t[t.reason=='deployment'].iloc[0]
        self.assertAlmostEqual(tagged_deployment_value(a,d,2),.05*1.1**2)

    def test_staging_second_tranche_does_not_grow_with_new_harvests(self):
        states=[0]+[1]*62
        a,t=self.run_rule([0,.5]+[0]*61,ReserveRule('hwm',initial=.15),states)
        d=t[t.reason=='deployment']
        self.assertAlmostEqual(d.net_deployed.iloc[0],.05)
        self.assertAlmostEqual(d.net_deployed.iloc[1],.05)
        self.assertGreater(d.net_deployed.iloc[2],.05)
        self.assertAlmostEqual(a.reserve_wealth.iloc[-1],0)

    def test_fast_cohort_path_matches_ledger_with_costs_and_both_lags(self):
        cal=pd.bdate_range('2000-01-03',periods=101); ix=cal[1:]
        rng=np.random.default_rng(15)
        r=pd.Series(rng.normal(.001,.03,len(ix)),index=ix)
        cash=pd.Series(.0001,index=ix)
        p=pd.Series(([0]*5+[1]*30)*3,index=pd.bdate_range('2000-01-04',periods=105)).loc[ix]
        dd=pd.Series(np.minimum(0,np.linspace(.1,-.55,len(ix))),index=ix)
        for lag in (1,2):
            for rule in (ReserveRule(),ReserveRule('fixed'),ReserveRule('hwm'),
                         ReserveRule('band',initial=.1,cap=.2),
                         ReserveRule('hwm',deployment='drawdown',initial=.15)):
                a,_=simulate_reserve(r,cash,p,dd,cal,rule,lag=lag,cost_bps=25)
                b=simulate_reserve(r,cash,p,dd,cal,rule,lag=lag,cost_bps=25,ledger=False)
                np.testing.assert_allclose(a.wealth,b)
                prior=a.wealth.shift(1,fill_value=1)
                expected=prior*(1-a.cost_fraction)*((1-a.start_reserve_weight)*(1+r)+a.start_reserve_weight*(1+cash))
                np.testing.assert_allclose(a.wealth,expected)

    def test_invalid_inputs_and_missing_calendar(self):
        with self.assertRaises(ValueError): self.run_rule([0,-1],ReserveRule())
        with self.assertRaises(ValueError): ReserveRule('unknown')

if __name__=='__main__': unittest.main()
