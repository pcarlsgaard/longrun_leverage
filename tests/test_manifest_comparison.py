"""The manifest check must ignore what is environmental and catch what is not."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'compare_manifests.py'
sys.path.insert(0, str(ROOT / 'scripts'))

from compare_manifests import differences, essential  # noqa: E402


def run():
    return subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, cwd=ROOT)


class EssentialFieldTests(unittest.TestCase):
    def test_nested_runtime_block_is_ignored(self):
        """The CI failure this fixes: runtime is nested, not a top-level key."""
        a = {'as_of': '2026-09-02', 'runtime': {'python': '3.11.15', 'numpy': '2.4.6'}}
        b = {'as_of': '2026-09-02', 'runtime': {'python': '3.12.14', 'numpy': '2.5.3'}}
        self.assertEqual(essential(a), essential(b))
        self.assertEqual(differences(essential(a), essential(b)), [])

    def test_generated_output_digest_is_ignored(self):
        """It hashes a file whose last digit legitimately moves between platforms."""
        a = {'cohort_rows': 20160, 'summary_sha256': 'a' * 64}
        b = {'cohort_rows': 20160, 'summary_sha256': 'b' * 64}
        self.assertEqual(differences(essential(a), essential(b)), [])

    def test_input_digests_are_still_compared(self):
        """config_sha256 and input_bundle_sha256 hash inputs, not outputs."""
        for key in ('config_sha256', 'input_bundle_sha256'):
            a, b = {key: 'a' * 64}, {key: 'b' * 64}
            self.assertTrue(differences(essential(a), essential(b)), key)

    def test_source_hash_change_is_caught(self):
        a = {'source_hashes': {'src/letf/model.py': 'a' * 64}}
        b = {'source_hashes': {'src/letf/model.py': 'b' * 64}}
        problems = differences(essential(a), essential(b))
        self.assertEqual(len(problems), 1)
        self.assertIn('source_hashes.src/letf/model.py', problems[0])

    def test_added_and_removed_keys_are_caught(self):
        self.assertIn('lags: removed', differences(essential({'lags': [1, 2]}), essential({})))
        self.assertIn('lags: added', differences(essential({}), essential({'lags': [1, 2]})))

    def test_parameter_grid_change_is_caught(self):
        a = {'grid': {'sma_days': [150, 200, 250]}}
        b = {'grid': {'sma_days': [150, 200]}}
        self.assertTrue(differences(essential(a), essential(b)))


class CommittedManifestTests(unittest.TestCase):
    def test_passes_on_the_committed_tree(self):
        self.assertEqual(run().returncode, 0)

    def test_catches_a_stale_source_hash(self):
        target = ROOT / 'reports' / 'capital_reserve_manifest.json'
        original = target.read_bytes()
        try:
            data = json.loads(original)
            key = next(iter(data['source_hashes']))
            data['source_hashes'][key] = '0' * 64
            target.write_text(json.dumps(data, indent=2) + '\n')
            result = run()
            self.assertEqual(result.returncode, 1)
            self.assertIn('capital_reserve_manifest.json', result.stderr)
        finally:
            target.write_bytes(original)

    def test_a_different_runtime_alone_does_not_fail(self):
        target = ROOT / 'reports' / 'capital_reserve_manifest.json'
        original = target.read_bytes()
        try:
            data = json.loads(original)
            data['runtime'] = {'python': '3.14.0', 'numpy': '9.9.9', 'pandas': '9.9.9'}
            target.write_text(json.dumps(data, indent=2) + '\n')
            self.assertEqual(run().returncode, 0)
        finally:
            target.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
