#!/usr/bin/env python3
"""Which regeneration steps actually have to run.

Regenerating every result takes most of an hour, and almost all of it is spent
proving again that code nobody touched still produces the bytes already in git.
This decides, per step, whether that proof is still standing.

**The guarantee is not weakened, because nothing is taken on trust.** A step is
skipped only when three things hold at once:

- every source file its manifest hashed is present and hashes the same, *and*
  the import graph has not gained or lost a file since — so the code that ran is
  the code that is here;
- every input file its manifest hashed still hashes the same — so the data that
  ran is the data that is here;
- every committed output its manifest hashed still hashes the same — so nobody
  has edited a result by hand.

If any of those fails the step runs and its results are compared as before. The
manifests themselves are only ever written by a real run, so "skipped" means
"already proven by the run that wrote this manifest", not "assumed fine".

A step whose manifest records no source hashes cannot be checked this way and
always runs. That is a property of those modules, not a hole in the mechanism,
and `tests/test_stale_modules.py` pins which ones they are so the list cannot
quietly grow.

Usage:
    python scripts/stale_modules.py          # names of the steps that must run
    python scripts/stale_modules.py --all    # every step, for a full rebuild
    python scripts/stale_modules.py --why    # names with the reason, to stderr
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from letf.provenance import sha, source_hashes  # noqa: E402


@dataclass(frozen=True)
class Step:
    """One command in `scripts/regenerate.sh`, and how to tell if it is stale.

    `manifest` is the file the step writes that records what produced its
    results; `module` is the dotted name whose import graph that manifest
    hashed. A step with neither cannot be checked and always runs.

    `scalar_inputs` maps a manifest field holding a single hash to the file it
    hashes, for the few manifests that record inputs that way rather than as a
    path-to-hash mapping.
    """
    name: str
    manifest: str | None = None
    module: str | None = None
    scalar_inputs: dict = field(default_factory=dict)


STEPS = (
    Step('analysis'),
    Step('falsification'),
    Step('reserve', 'capital_reserve_manifest.json', 'letf.capital_reserve'),
    Step('reserve-cohorts', 'capital_reserve_fresh_manifest.json', 'letf.reserve_cohorts',
         {'input_bundle_sha256': 'data/snapshots/portfolio_sma_inputs.zip',
          'config_sha256': 'config.json'}),
    Step('regime-signals', 'regime_signal_manifest.json', 'letf.regime_signals'),
    Step('price-signal-revision'),
    Step('cohort-distributions'),
    Step('cross-index-signal'),
    Step('null-model'),
    Step('hedge-alternatives', 'hedge_alternatives_manifest.json',
         'letf.hedge_alternatives'),
    Step('leaps-robustness', 'leaps_roll_monte_carlo_manifest.json',
         'letf.leaps_robustness'),
    Step('treasury-leverage', 'leaps_treasury_leverage_manifest.json',
         'letf.treasury_leverage'),
    Step('lflr-reproduction', 'lflr_reproduction_manifest.json', 'letf.lflr_reproduction'),
    Step('nasdaq-leaps', 'nasdaq_leaps_manifest.json', 'letf.nasdaq_leaps'),
    Step('leaps-frontier', 'leaps_frontier_manifest.json', 'letf.leaps_frontier'),
    Step('xnd-short-maturity', 'xnd_short_maturity_manifest.json',
         'letf.xnd_short_maturity'),
)
NAMES = tuple(step.name for step in STEPS)


def _hashes_match(root: Path, recorded: dict, kind: str):
    """The first recorded file that is missing or has changed, described."""
    for relative, digest in recorded.items():
        path = root / relative
        if not path.exists():
            return f'{kind} {relative} is gone'
        if sha(path) != digest:
            return f'{kind} {relative} changed'
    return None


def reason(root: Path, step: Step) -> str | None:
    """Why this step must run, or None when its committed results still stand."""
    if step.manifest is None:
        return 'records no manifest to check against'
    path = root / 'reports' / step.manifest
    if not path.exists():
        return f'{step.manifest} is missing'
    manifest = json.loads(path.read_text())

    recorded = manifest.get('source_hashes')
    if not recorded:
        return f'{step.manifest} records no source hashes'
    changed = _hashes_match(root, recorded, 'source')
    if changed:
        return changed
    # A new import adds a file to the graph without changing any file already
    # hashed, so the key sets have to be compared as well as the values.
    if set(recorded) != set(source_hashes(root, step.module)):
        return 'the import graph changed'

    changed = _hashes_match(root, manifest.get('input_hashes', {}), 'input')
    if changed:
        return changed
    for field_name, relative in step.scalar_inputs.items():
        if field_name in manifest and sha(root / relative) != manifest[field_name]:
            return f'input {relative} changed'

    outputs = {f'reports/{name}': digest
               for name, digest in manifest.get('outputs_sha256', {}).items()}
    return _hashes_match(root, outputs, 'output')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--all', action='store_true',
                        help='every step, for a full rebuild')
    parser.add_argument('--why', action='store_true',
                        help='also explain each choice on stderr')
    args = parser.parse_args()

    run = []
    for step in STEPS:
        why = 'rebuilding everything' if args.all else reason(args.root, step)
        if why:
            run.append(step.name)
        if args.why:
            print(f'{step.name}: {why or "already proven by its manifest"}',
                  file=sys.stderr)
    print(' '.join(run))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
