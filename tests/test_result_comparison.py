"""The reproducibility gate has to fail on the things it exists to catch."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'compare_results.py'


def run():
    return subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, cwd=ROOT)


class ComparatorTests(unittest.TestCase):
    """Each test perturbs a committed result, runs the check, and restores it."""

    target = ROOT / 'reports' / 'sma_strategy_metrics.csv'

    def setUp(self):
        self.original = self.target.read_bytes()

    def tearDown(self):
        self.target.write_bytes(self.original)

    def test_passes_on_the_committed_tree(self):
        self.assertEqual(run().returncode, 0)

    def test_catches_a_changed_number(self):
        text = self.original.decode()
        first, second, rest = text.split('\n', 2)
        cells = second.split(',')
        cells[8] = '0.5'                       # a CAGR, moved far outside tolerance
        self.target.write_text('\n'.join([first, ','.join(cells), rest]))
        result = run()
        self.assertEqual(result.returncode, 1)
        self.assertIn('sma_strategy_metrics.csv', result.stderr)

    def test_tolerates_last_digit_platform_noise(self):
        """0.112646685 -> 0.1126466851 is ~1e-9 relative: numpy build noise, not a change."""
        text = self.original.decode()
        first, second, rest = text.split('\n', 2)
        cells = second.split(',')
        cells[8] = repr(float(cells[8]) * (1 + 2e-10))
        self.target.write_text('\n'.join([first, ','.join(cells), rest]))
        self.assertEqual(run().returncode, 0)

    def test_catches_a_changed_label(self):
        self.target.write_text(self.original.decode().replace('SSO_ALWAYS', 'SSO_RENAMED', 1))
        result = run()
        self.assertEqual(result.returncode, 1)
        self.assertIn('text differs', result.stderr)

    def test_catches_a_dropped_row(self):
        lines = self.original.decode().split('\n')
        self.target.write_text('\n'.join(lines[:2] + lines[3:]))
        result = run()
        self.assertEqual(result.returncode, 1)
        self.assertIn('row count changed', result.stderr)

    def test_catches_a_hand_edited_markdown_report(self):
        report = ROOT / 'reports' / 'sma_falsification_results.md'
        original = report.read_bytes()
        try:
            report.write_text(original.decode() + '\nA paragraph nobody generated.\n')
            result = run()
            self.assertEqual(result.returncode, 1)
            self.assertIn('sma_falsification_results.md', result.stderr)
        finally:
            report.write_bytes(original)

    def test_catches_a_result_that_no_script_produces(self):
        orphan = ROOT / 'reports' / 'not_generated_by_anything.csv'
        try:
            orphan.write_text('a,b\n1,2\n')
            result = run()
            self.assertEqual(result.returncode, 1)
            self.assertIn('not committed', result.stderr)
        finally:
            orphan.unlink()


if __name__ == '__main__':
    unittest.main()
