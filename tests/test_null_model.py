import unittest

import numpy as np
import pandas as pd

from letf.diagnostics import edge_concentration, log_advantage
from letf.null_model import SPECIFICATIONS, episodes, permuted_position, total_cagr


class PermutationTests(unittest.TestCase):
    def setUp(self):
        self.ix = pd.bdate_range('2020-01-01', periods=60)
        rng = np.random.default_rng(7)
        self.position = pd.Series(rng.integers(0, 2, len(self.ix)).astype(float), index=self.ix)

    def test_episodes_partition_the_series(self):
        blocks = episodes(self.position)
        self.assertEqual(sum(length for _, length in blocks), len(self.position))
        rebuilt = np.concatenate([np.full(n, s) for s, n in blocks])
        np.testing.assert_array_equal(rebuilt, self.position.to_numpy())

    def test_permutation_preserves_exposure_switches_and_episode_lengths(self):
        rng = np.random.default_rng(1)
        for _ in range(25):
            q = permuted_position(self.position, rng)
            self.assertAlmostEqual(q.mean(), self.position.mean())
            self.assertEqual((q.diff() != 0).sum(), (self.position.diff() != 0).sum())
            self.assertEqual(sorted(n for _, n in episodes(q)),
                             sorted(n for _, n in episodes(self.position)))
            self.assertTrue(q.index.equals(self.position.index))

    def test_permutation_actually_moves_timing(self):
        rng = np.random.default_rng(2)
        drawn = [permuted_position(self.position, rng) for _ in range(20)]
        self.assertTrue(any(not q.equals(self.position) for q in drawn))

    def test_single_episode_has_no_permutation(self):
        flat = pd.Series(1., index=self.ix)
        pd.testing.assert_series_equal(permuted_position(flat, np.random.default_rng(0)), flat)

    def test_specification_count_matches_the_battery_grid(self):
        """SMA length x lag x spread x switch cost x off-asset."""
        self.assertEqual(SPECIFICATIONS, 144)

    def test_total_cagr_recovers_a_known_rate(self):
        calendar = pd.bdate_range('2000-01-03', '2010-01-29')
        r = pd.Series(1.08 ** (1 / 261) - 1, index=calendar[1:])
        self.assertAlmostEqual(total_cagr(r, calendar), .08, places=2)

    def test_total_cagr_of_a_wiped_out_sleeve_is_total_loss(self):
        calendar = pd.bdate_range('2000-01-03', '2010-01-29')
        r = pd.Series(0., index=calendar[1:])
        r.iloc[10] = -1.
        self.assertEqual(total_cagr(r, calendar), -1.0)


class EdgeConcentrationTests(unittest.TestCase):
    def setUp(self):
        self.ix = pd.bdate_range('2020-01-01', periods=100)

    def test_log_advantage_sums_to_the_total_gap(self):
        rng = np.random.default_rng(3)
        a = pd.Series(rng.normal(.001, .01, 100), index=self.ix)
        b = pd.Series(rng.normal(.000, .01, 100), index=self.ix)
        self.assertAlmostEqual(float(log_advantage(a, b).sum()),
                               float(np.log((1 + a).prod() / (1 + b).prod())))

    def test_one_dominant_session_is_reported_as_the_whole_edge(self):
        a = pd.Series(0., index=self.ix)
        b = pd.Series(0., index=self.ix)
        a.iloc[42] = .5
        row = edge_concentration(a, b)
        self.assertAlmostEqual(row['top1_day_share'], 1.0)
        self.assertEqual(row['best_day'], self.ix[42].date().isoformat())
        self.assertAlmostEqual(row['advantage_excluding_top_month'], 0.0)

    def test_evenly_spread_edge_has_a_small_top_day_share(self):
        a = pd.Series(.001, index=self.ix)
        b = pd.Series(0., index=self.ix)
        row = edge_concentration(a, b)
        self.assertAlmostEqual(row['top1_day_share'], 1 / 100, places=6)
        self.assertAlmostEqual(row['top5_day_share'], 5 / 100, places=6)

    def test_zero_advantage_gives_undefined_shares_not_a_division_blowup(self):
        a = pd.Series(.001, index=self.ix)
        row = edge_concentration(a, a)
        self.assertTrue(np.isnan(row['top1_day_share']))
        self.assertEqual(row['total_log_advantage'], 0.0)

    def test_mismatched_calendars_are_rejected(self):
        a = pd.Series(.001, index=self.ix)
        with self.assertRaises(ValueError):
            edge_concentration(a, a.iloc[:-1])


if __name__ == '__main__':
    unittest.main()
