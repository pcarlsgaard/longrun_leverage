"""Check that each manifest still describes what produced the results beside it.

A manifest's job is to answer "what inputs and what code made this?". Two
classes of field cannot answer that and are excluded:

  Runtime versions — recorded on purpose, environmental by definition. A
  manifest written on Python 3.11/numpy 2.4.6 should not be called stale by a
  runner on 3.12/numpy 2.5.3.

  Hashes of generated outputs — these inherit the last-digit float noise that
  scripts/compare_results.py handles with a tolerance, so requiring them to
  match would reintroduce the byte-identity requirement through the back door.
  The outputs themselves are already checked, properly, by that script.

Everything else is compared exactly: input hashes, source hashes, windows,
parameter grids, row counts. A change in any of those means the manifest is
describing a run that no longer happens.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Version strings, at any depth.
ENVIRONMENTAL = {'runtime', 'python', 'numpy', 'pandas', 'scipy', 'matplotlib', 'platform'}

# Digests of files this repository generates. Named explicitly rather than
# matched by pattern, because sibling keys like `config_sha256` and
# `input_bundle_sha256` hash *inputs* and must still be checked. A new output
# digest will fail this check until it is listed here, which is the right way
# round.
GENERATED_DIGESTS = {'summary_sha256', 'output_sha256'}

IGNORED = ENVIRONMENTAL | GENERATED_DIGESTS


def essential(value):
    if isinstance(value, dict):
        return {k: essential(v) for k, v in value.items() if k not in IGNORED}
    if isinstance(value, list):
        return [essential(v) for v in value]
    return value


def differences(old: dict, new: dict) -> list[str]:
    problems = []
    for key in sorted(set(old) | set(new)):
        if key not in old:
            problems.append(f'{key}: added')
        elif key not in new:
            problems.append(f'{key}: removed')
        elif old[key] != new[key]:
            if isinstance(old[key], dict) and isinstance(new[key], dict):
                inner = differences(old[key], new[key])
                problems.extend(f'{key}.{p}' for p in inner)
            else:
                problems.append(f'{key}: {old[key]!r} -> {new[key]!r}')
    return problems


def main() -> int:
    failures = {}
    for path in sorted((ROOT / 'reports').glob('*_manifest.json')):
        rel = str(path.relative_to(ROOT))
        show = subprocess.run(['git', 'show', f'HEAD:{rel}'], capture_output=True, text=True)
        if show.returncode != 0:
            failures[rel] = ['regenerated but not committed']
            continue
        problems = differences(essential(json.loads(show.stdout)),
                               essential(json.loads(path.read_text())))
        if problems:
            failures[rel] = problems

    if failures:
        print('Manifests no longer describe the run that produced these results:', file=sys.stderr)
        for rel, problems in sorted(failures.items()):
            print(f'\n  {rel}', file=sys.stderr)
            for problem in problems[:10]:
                print(f'    {problem}', file=sys.stderr)
            if len(problems) > 10:
                print(f'    ... and {len(problems) - 10} more', file=sys.stderr)
        return 1

    print('Every manifest records the inputs and sources that produced its results.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
