import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from stale_modules import NAMES, STEPS, reason  # noqa: E402

REGENERATE = ROOT / 'scripts/regenerate.sh'
# Steps that cannot be checked against a manifest and therefore always run.
# Pinned so the list cannot quietly grow: every entry here is a module that
# records no source hashes, and adding one silently would erode the mechanism.
ALWAYS = {'analysis', 'falsification', 'price-signal-revision',
          'cohort-distributions', 'cross-index-signal', 'null-model'}


class ConfigurationTests(unittest.TestCase):
    def test_step_names_are_unique(self):
        self.assertEqual(len(NAMES), len(set(NAMES)))

    def test_regenerate_gates_every_step_and_no_others(self):
        """The script and the checker must not drift apart."""
        text = REGENERATE.read_text()
        gated = {line.split()[2].rstrip(';') for line in text.splitlines()
                 if line.startswith('if needs ')}
        self.assertEqual(gated, set(NAMES))

    def test_every_step_runs_exactly_one_command(self):
        text = REGENERATE.read_text()
        for name in NAMES:
            self.assertEqual(text.count(f'if needs {name};'), 1, name)

    def test_the_unverifiable_steps_are_the_ones_pinned_here(self):
        without = {step.name for step in STEPS if step.manifest is None}
        self.assertEqual(without, ALWAYS)

    def test_every_checkable_step_names_a_module_and_a_manifest(self):
        for step in STEPS:
            if step.manifest is None:
                self.assertIsNone(step.module, step.name)
            else:
                self.assertTrue(step.module.startswith('letf.'), step.name)
                self.assertTrue((ROOT / 'reports' / step.manifest).exists()
                                or step.name == 'xnd-short-maturity', step.manifest)

    def test_every_manifest_recording_sources_is_claimed_by_a_step(self):
        claimed = {step.manifest for step in STEPS if step.manifest}
        for path in (ROOT / 'reports').glob('*.json'):
            if 'source_hashes' in json.loads(path.read_text()):
                self.assertIn(path.name, claimed, path.name)


class DetectionTests(unittest.TestCase):
    """Every kind of change that must force a rerun, on a throwaway copy."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / 'repo'
        for part in ('src', 'reports', 'scripts', 'data', 'config.json'):
            source = ROOT / part
            target = cls.root / part
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def stale(self):
        return {step.name for step in STEPS if reason(self.root, step)}

    def test_the_committed_tree_needs_only_the_unverifiable_steps(self):
        self.assertEqual(self.stale() - {'xnd-short-maturity'}, ALWAYS)

    def test_a_shared_source_change_restales_everything_importing_it(self):
        path = self.root / 'src/letf/options.py'
        original = path.read_bytes()
        path.write_bytes(original + b'\n# probe\n')
        try:
            new = self.stale() - ALWAYS - {'xnd-short-maturity'}
        finally:
            path.write_bytes(original)
        self.assertIn('leaps-frontier', new)
        self.assertIn('hedge-alternatives', new)
        # Modules that do not import it must not be dragged in.
        self.assertNotIn('reserve', new)
        self.assertNotIn('regime-signals', new)
        self.assertEqual(self.stale() - {'xnd-short-maturity'}, ALWAYS)

    def test_a_hand_edited_result_restales_its_own_step_only(self):
        path = self.root / 'reports/leaps_frontier_classification.csv'
        original = path.read_bytes()
        path.write_bytes(original + b'\n')
        try:
            new = self.stale() - ALWAYS - {'xnd-short-maturity'}
        finally:
            path.write_bytes(original)
        self.assertEqual(new, {'leaps-frontier'})

    def test_a_new_import_restales_even_though_no_hashed_file_changed(self):
        path = self.root / 'src/letf/nasdaq_leaps.py'
        original = path.read_bytes()
        path.write_bytes(original.replace(b'from .model import portfolio',
                                          b'from .model import portfolio\n'
                                          b'from .diagnostics import *', 1))
        try:
            new = self.stale() - ALWAYS - {'xnd-short-maturity'}
        finally:
            path.write_bytes(original)
        self.assertIn('nasdaq-leaps', new)
        self.assertIn('leaps-frontier', new)

    def test_a_changed_input_restales_its_step(self):
        path = self.root / 'config.json'
        original = path.read_bytes()
        path.write_bytes(original + b'\n')
        try:
            new = self.stale() - ALWAYS - {'xnd-short-maturity'}
        finally:
            path.write_bytes(original)
        self.assertIn('reserve', new)
        self.assertIn('reserve-cohorts', new)

    def test_a_missing_manifest_restales_its_step(self):
        path = self.root / 'reports/nasdaq_leaps_manifest.json'
        original = path.read_bytes()
        path.unlink()
        try:
            self.assertIn('nasdaq-leaps', self.stale())
        finally:
            path.write_bytes(original)

    def test_a_deleted_source_file_is_reported_rather_than_crashing(self):
        path = self.root / 'src/letf/cohorts.py'
        original = path.read_bytes()
        path.unlink()
        try:
            self.assertIn('gone', reason(self.root, next(
                s for s in STEPS if s.name == 'leaps-frontier')))
        finally:
            path.write_bytes(original)


class CommandTests(unittest.TestCase):
    def run_checker(self, *args):
        out = subprocess.run([sys.executable, str(ROOT / 'scripts/stale_modules.py'),
                              *args], capture_output=True, text=True, check=True)
        return out.stdout.split()

    def test_all_lists_every_step_in_order(self):
        self.assertEqual(self.run_checker('--all'), list(NAMES))

    def test_the_default_is_a_subset_of_every_step(self):
        self.assertTrue(set(self.run_checker()) <= set(NAMES))

    def test_why_explains_every_step(self):
        out = subprocess.run([sys.executable, str(ROOT / 'scripts/stale_modules.py'),
                              '--why'], capture_output=True, text=True, check=True)
        for name in NAMES:
            self.assertIn(f'{name}: ', out.stderr)
