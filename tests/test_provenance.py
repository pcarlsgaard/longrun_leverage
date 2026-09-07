import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from letf.provenance import FLOAT_FORMAT, ZERO_TOLERANCE, sha, source_hashes, stable_floats

ROOT = Path(__file__).resolve().parents[1]


class StableFloatTests(unittest.TestCase):
    """Round-off must print as zero, or results are not byte-reproducible.

    %g prints its significant digits whatever the exponent, so an algebraically
    zero difference prints as -7.993605777e-14 on one platform and
    -8.348877145e-14 on another: no agreement in any digit.
    """

    def test_round_off_becomes_exactly_zero(self):
        frame = pd.DataFrame({'residual': [-7.993605777e-14, 8.3e-15, 0.0]})
        np.testing.assert_array_equal(stable_floats(frame).residual, [0.0, 0.0, 0.0])

    def test_real_quantities_are_untouched(self):
        frame = pd.DataFrame({'cagr': [0.1126466854, -0.5525024107, 1835.639381, 1e-6]})
        pd.testing.assert_frame_equal(stable_floats(frame), frame)

    def test_snapping_is_applied_per_value_not_per_column(self):
        frame = pd.DataFrame({'benefit': [2.038848473, -7.99e-14, 0.06297740447]})
        np.testing.assert_allclose(stable_floats(frame).benefit,
                                   [2.038848473, 0.0, 0.06297740447])

    def test_non_numeric_columns_survive(self):
        frame = pd.DataFrame({'series': ['UPRO', 'SSO'], 'residual': [1e-15, 0.5]})
        out = stable_floats(frame)
        self.assertEqual(list(out.series), ['UPRO', 'SSO'])
        self.assertEqual(list(out.residual), [0.0, 0.5])

    def test_the_threshold_sits_in_empty_space(self):
        """Observed round-off tops out near 1e-10; real values start near 1e-6."""
        self.assertGreater(ZERO_TOLERANCE, 1e-10)
        self.assertLess(ZERO_TOLERANCE, 1e-6)

    def test_committed_results_contain_no_values_inside_the_gap(self):
        """If this fails, a real quantity has drifted down toward the threshold."""
        offenders = []
        for path in sorted((ROOT / 'reports').glob('*.csv')):
            values = pd.read_csv(path).select_dtypes('number').abs().to_numpy().ravel()
            values = values[np.isfinite(values) & (values > 0)]
            if ((values >= ZERO_TOLERANCE) & (values < 1e-7)).any():
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_output_precision_is_inside_float64_stability(self):
        self.assertEqual(FLOAT_FORMAT, '%.10g')


class SourceHashTests(unittest.TestCase):
    def test_graph_includes_dependencies_and_excludes_strangers(self):
        hashes = source_hashes(ROOT, 'letf.capital_reserve')
        names = {Path(k).stem for k in hashes}
        self.assertLessEqual({'capital_reserve', 'reserve', 'cohorts', 'model', 'provenance'}, names)
        # Lazily imported inside run(), so a sys.modules walk would miss it.
        self.assertIn('reserve_report', names)
        # Never imported by this module; hashing it made the manifest churn.
        self.assertNotIn('null_model', names)
        self.assertNotIn('cross_index_signal', names)

    def test_hashes_match_the_files_on_disk(self):
        for rel, digest in source_hashes(ROOT, 'letf.null_model').items():
            self.assertEqual(digest, sha(ROOT / rel))


if __name__ == '__main__':
    unittest.main()


class ModuleNameGuardTests(unittest.TestCase):
    """`__name__` is "__main__" under `python -m`, which silently hashed nothing."""

    def test_main_is_rejected_rather_than_returning_nothing(self):
        with self.assertRaises(ValueError):
            source_hashes(ROOT, '__main__')

    def test_a_bare_name_is_rejected(self):
        with self.assertRaises(ValueError):
            source_hashes(ROOT, 'letf')

    def test_every_manifest_writer_passes_a_dotted_name(self):
        import re
        offenders = []
        for path in sorted((ROOT / 'src' / 'letf').glob('*.py')):
            if re.search(r'source_hashes\(\s*root\s*,\s*__name__\s*\)', path.read_text()):
                offenders.append(path.name)
        self.assertEqual(offenders, [])
