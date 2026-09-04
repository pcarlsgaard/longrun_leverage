import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from letf.analysis import (tbill_accrual, sma_position, matched, state_metrics,
                           drawdown_details, extended_metrics, worst_rolling,
                           cohort_summary, rotations, CASH)
from letf.model import portfolio, wealth, calendar_days


class AnalysisTests(unittest.TestCase):
    def test_exact_200_level_warmup_and_one_session_lag(self):
        calendar = pd.bdate_range('2020-01-01', periods=205)
        r = pd.Series(.001, index=calendar[1:])
        p = sma_position(r, calendar, 200)
        self.assertTrue(p.iloc[:200].isna().all())
        self.assertEqual(p.iloc[200], 1.)
        changed = r.copy()
        changed.loc[calendar[200]] = -.9
        q = sma_position(changed, calendar, 200)
        # Today's crash cannot affect today's position, only tomorrow's.
        pd.testing.assert_series_equal(p.iloc[:201], q.iloc[:201])
        self.assertEqual(q.iloc[201], 0.)
        changed.loc[calendar[202]:] = 10.
        z = sma_position(changed, calendar, 200)
        pd.testing.assert_series_equal(q.iloc[:203], z.iloc[:203])

    def test_equality_is_off(self):
        calendar = pd.bdate_range('2020-01-01', periods=205)
        p = sma_position(pd.Series(0., index=calendar[1:]), calendar)
        self.assertTrue((p.dropna() == 0).all())

    def test_bill_discount_conversion_weekend_and_lag(self):
        prior = pd.Timestamp('2024-01-05')
        ix = pd.to_datetime(['2024-01-08', '2024-01-09'])
        rates = pd.Series([.036, .072, .9], index=pd.to_datetime(['2024-01-05', '2024-01-08', '2024-01-09']))
        r = tbill_accrual(ix, prior, rates)
        c = lambda d: (1/(1-d*91/360)-1)/91
        self.assertAlmostEqual(r.iloc[0], (1+c(.036))**3-1)
        self.assertAlmostEqual(r.iloc[1], c(.072))
        with self.assertRaises(ValueError):
            tbill_accrual(ix, prior, rates.iloc[1:])

    def test_switch_count_and_episodes(self):
        ix = pd.bdate_range('2024-01-02', periods=6)
        p = pd.Series([1, 1, 0, 0, 0, 1], index=ix)
        d = pd.Series(1., index=ix)
        m = state_metrics(pd.Series(.01, index=ix), p, d, 'tbill')
        self.assertEqual(m['switches'], 2)
        self.assertEqual(m['fraction_tbill'], .5)
        self.assertEqual(m['average_risk_on_episode_sessions'], 1.5)
        self.assertEqual(m['average_risk_off_episode_sessions'], 3.)
        self.assertAlmostEqual(m['risk_on_state_cagr'], 1.01**365.25-1)
        self.assertEqual(state_metrics(pd.Series(0.,index=ix),p*0,d,'tbill')['switches'], 0)

    def test_underwater_recovery_and_censoring(self):
        ix = pd.to_datetime(['2020-01-01','2020-01-03','2020-01-05','2020-01-09','2020-01-15'])
        nav = pd.Series([1, 1.2, .6, 1.2, 1.1], index=ix)
        d = drawdown_details(nav)
        self.assertEqual(d['longest_underwater_calendar_days'], 6)
        self.assertEqual(d['max_drawdown_recovery_days_from_trough'], 4)
        self.assertEqual(d['max_drawdown_recovery_days_from_peak'], 6)
        nav.iloc[3:] = [.7, .8]
        d = drawdown_details(nav)
        self.assertEqual(d['longest_underwater_calendar_days'], 12)
        self.assertFalse(d['max_drawdown_recovered'])
        self.assertTrue(np.isnan(d['max_drawdown_recovery_days_from_trough']))
        self.assertEqual(drawdown_details(pd.Series([1,2,3,4,5],index=ix))['longest_underwater_calendar_days'], 0)

    def test_quarterly_monthly_annual_rebalancing(self):
        ix = pd.to_datetime(['2020-01-31','2020-02-03','2020-03-31','2020-04-01'])
        r = pd.DataFrame({'a':[1., -.5, 1., -.5], 'b':[0., 0., 0., 0.]}, index=ix)
        w = {'a':.5, 'b':.5}
        self.assertAlmostEqual(wealth(portfolio(r,w,'annual')).iloc[-1],1.)
        self.assertAlmostEqual(wealth(portfolio(r,w,'quarterly')).iloc[-1],1.125)
        # Monthly resets at Feb, Mar and Apr boundaries: .75*1.5*.75.
        self.assertAlmostEqual(wealth(portfolio(r,w,'monthly')).iloc[-1],1.5*.75*1.5*.75)

    def test_calendar_alignment_rejects_holes(self):
        ix = pd.bdate_range('2020-01-01',periods=6)
        frame = pd.DataFrame({'a':[np.nan,1,2,3,4,5], 'b':[np.nan,np.nan,1,2,3,np.nan]},index=ix)
        self.assertTrue(matched(frame).index.equals(ix[2:5]))
        frame.loc[ix[3],'a'] = np.nan
        with self.assertRaises(ValueError): matched(frame)
        r = pd.Series(.01,index=ix[1:])
        with self.assertRaises(ValueError): extended_metrics(r,r.iloc[1:],ix)
        with self.assertRaises(ValueError): extended_metrics(r.drop(ix[3]),r.drop(ix[3]),ix)

    def test_calendar_anniversary_cohorts(self):
        calendar = pd.bdate_range('1990-01-01','2024-02-02')
        days = calendar_days(calendar[1:],calendar[0])
        r = (1.1**(days/365.25)-1).rename('constant')
        summary, cohorts = cohort_summary(r.to_frame(),calendar)
        np.testing.assert_allclose(cohorts.cagr,.1,atol=1e-12)
        self.assertEqual(set(summary.horizon_years),{20,30})
        for row in cohorts.iloc[[0,-1]].itertuples():
            anniversary = pd.Timestamp(row.entry_close)+pd.DateOffset(years=row.horizon_years)
            self.assertEqual(pd.Timestamp(row.exit_close),calendar[calendar.searchsorted(anniversary)])
            self.assertAlmostEqual(row.multiple,1.1**((pd.Timestamp(row.exit_close)-pd.Timestamp(row.entry_close)).days/365.25))
        nav = pd.concat([pd.Series([1.], index=calendar[:1]),wealth(r)])
        self.assertAlmostEqual(worst_rolling(nav,5),.1)

    def test_financing_only_applies_when_leveraged(self):
        ix = pd.bdate_range('2020-01-01',periods=4)
        daily = pd.DataFrame({'SP500_1X':[.01]*4,CASH:[.001]*4,
                              'UPRO_SPREAD_0BP':[.03]*4,'UPRO_SPREAD_100BP':[.029]*4},index=ix)
        positions = {('SP500',200):pd.Series([1.,0.,1.,0.],index=ix)}
        a,_ = rotations(daily,positions,200,0,ix,['UPRO'])
        b,_ = rotations(daily,positions,200,100,ix,['UPRO'])
        np.testing.assert_allclose(a.UPRO_SMA_TO_TBILL-b.UPRO_SMA_TO_TBILL,[.001,0,.001,0])
        np.testing.assert_allclose(a.UPRO_SMA_TO_SP500,[.03,.01,.03,.01])

if __name__ == '__main__':
    unittest.main()
