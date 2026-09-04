import unittest
import numpy as np
import pandas as pd

from letf.regime_signals import signal_features, positions, regression_quality, matched_control
from letf.falsification import select_returns, switching_costs


class RegimeTests(unittest.TestCase):
    def test_known_paths_and_regression(self):
        ix = pd.bdate_range('2000-01-01', periods=200)
        price = pd.Series(np.exp(np.arange(200)*.001), index=ix)
        f = signal_features(price, price.pct_change(), price)
        self.assertAlmostEqual(f.er.iloc[-1], 1.)
        self.assertAlmostEqual(f.ser.iloc[-1], 1.)
        self.assertAlmostEqual(f.slope.iloc[-1], .001)
        self.assertAlmostEqual(f.r_squared.iloc[-1], 1.)
        self.assertTrue(np.isposinf(f.slope_t.iloc[-1]))
        self.assertEqual(f.flip_rate.iloc[-1], 0.)
        self.assertAlmostEqual(f.ao.iloc[-1],price.iloc[-5:].mean()-price.iloc[-34:].mean())
        flat = signal_features(price*0+100, price*0)
        self.assertEqual(flat.er.iloc[-1],0.)
        self.assertEqual(flat.slope_t.iloc[-1],0.)
        alternating = pd.Series([100.,101.]*100,index=ix)
        alt = signal_features(alternating,alternating.pct_change())
        self.assertEqual(alt.flip_rate.iloc[-1],1.)
        self.assertEqual(alt.er.iloc[-1],0.)
        down = signal_features(1/price,(1/price).pct_change())
        self.assertAlmostEqual(down.ser.iloc[-1],-1.)
        # Independently calculate non-perfect OLS, including residual df.
        noisy = price*np.exp(.02*np.sin(np.arange(200)))
        q = regression_quality(noisy).iloc[-1]
        x = np.arange(120); y = np.log(noisy.iloc[-120:])
        slope, intercept = np.polyfit(x,y,1)
        sse = np.square(y-(intercept+slope*x)).sum()
        self.assertAlmostEqual(q.slope_t,slope/np.sqrt(sse/118/np.square(x-x.mean()).sum()))

    def test_lagging_and_future_perturbation_all_signals(self):
        ix = pd.bdate_range('2000-01-01',periods=400)
        rng = np.random.default_rng(54)
        price = pd.Series(np.exp(np.cumsum(rng.normal(.001,.02,400))),index=ix)
        r = price.pct_change(); daily = pd.DataFrame({'SP500_1X':r,'UPRO_BASE':3*r},index=ix)
        features = signal_features(price,r,price)
        p1, p2 = positions(daily,features,1),positions(daily,features,2)
        pd.testing.assert_frame_equal(p2,p1.shift(1))
        altered = price.copy(); altered.iloc[300:] *= np.linspace(2,10,100)
        changed = daily.copy(); changed.SP500_1X = altered.pct_change()
        other = signal_features(altered,changed.SP500_1X,altered)
        pd.testing.assert_frame_equal(features.iloc[:300],other.iloc[:300])
        for lag in (1,2):
            p, q = positions(daily,features,lag),positions(changed,other,lag)
            pd.testing.assert_frame_equal(p.iloc[:300+lag],q.iloc[:300+lag])
        self.assertAlmostEqual(features.flip_median.iloc[300],features.flip_rate.iloc[:300].median())
        self.assertAlmostEqual(features.relative_volatility.iloc[300],r.iloc[281:301].std()/r.iloc[181:301].std())
        # Holdings select the correct sleeve; a signal at close affects the next
        # return, and the added wait defers it one more session.
        for lag in (1,2):
            p = positions(daily,features,lag).EFFICIENCY.dropna()
            got = select_returns(daily,p,{0:'SP500_1X',1:'UPRO_BASE'})
            expected = daily.loc[p.index,'SP500_1X']*(1+2*p)
            np.testing.assert_allclose(got,expected)
            cost = switching_costs(got,p,25)
            switches = int(p.diff().abs().fillna(0).sum())
            self.assertAlmostEqual((1+cost).prod()/(1+got).prod(),.9975**switches)

    def test_matched_exposure_funding_and_fee_identity(self):
        ix = pd.bdate_range('2020-01-01',periods=6)
        u = pd.Series([.01,-.02,.005,.006,-.003,.001],index=ix)
        days = pd.Series([1,1,3,1,1,1],index=ix)
        expense=.009; fee=expense*days/365; funding=.04*days/365
        lev=3*u-2*funding-fee
        p=pd.Series([1.,0.,1.,0.,0.,1.],index=ix)
        const, neutral=matched_control(u,lev,days,expense,p)
        np.testing.assert_allclose(const,2*u-funding-fee)
        self.assertAlmostEqual((2*u-funding-neutral).sum(),(fee*p).sum())
        full,_=matched_control(u,lev,days,expense,p*0+1)
        np.testing.assert_allclose(full,lev)
