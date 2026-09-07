"""Compare committed results against what the code just produced.

Byte-identity is the wrong bar for numeric output. These results come from
accumulating arithmetic over ~10,000 trading sessions, and different numpy
builds reorder and vectorize that arithmetic differently. The observed
disagreement is ~2e-10 relative — the tenth significant figure — and no
choice of output precision makes that go away without also throwing away
digits that are real.

So this compares the way a person would: structure and text exactly, numbers
within a tolerance far below anything economically meaningful and far above
platform noise.

What it still catches, which is everything the check exists for:
  - a committed report that was edited by hand after generation
  - a committed result that no script produces (or that no longer produces it)
  - a code change that actually moved the numbers
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# 1e-8 relative is ~2 orders above the observed cross-platform noise (~2e-10)
# and ~5 orders below the smallest difference that would change a conclusion.
RTOL = 1e-8
ATOL = 1e-12
ROOT = Path(__file__).resolve().parents[1]


def committed(path: str) -> str | None:
    result = subprocess.run(['git', 'show', f'HEAD:{path}'], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def compare_frames(old: pd.DataFrame, new: pd.DataFrame) -> list[str]:
    if list(old.columns) != list(new.columns):
        return [f'columns changed: {list(old.columns)} -> {list(new.columns)}']
    if len(old) != len(new):
        return [f'row count changed: {len(old)} -> {len(new)}']

    problems = []
    numeric = [c for c in old.columns
               if pd.api.types.is_numeric_dtype(old[c]) and pd.api.types.is_numeric_dtype(new[c])]
    for column in old.columns:
        if column in numeric:
            a, b = old[column].to_numpy(float), new[column].to_numpy(float)
            close = np.isclose(a, b, rtol=RTOL, atol=ATOL, equal_nan=True)
            if not close.all():
                row = int(np.flatnonzero(~close)[0])
                problems.append(f'{column}: {int((~close).sum())} value(s) differ, '
                                f'first at row {row}: {a[row]!r} -> {b[row]!r}')
        elif not old[column].astype(str).equals(new[column].astype(str)):
            differing = old.index[old[column].astype(str) != new[column].astype(str)]
            row = int(differing[0])
            problems.append(f'{column}: text differs, first at row {row}: '
                            f'{old[column].iloc[row]!r} -> {new[column].iloc[row]!r}')
    return problems


def main() -> int:
    failures = {}
    for path in sorted(list((ROOT / 'reports').glob('*.csv')) + list((ROOT / 'reports').glob('*.md'))):
        rel = str(path.relative_to(ROOT))
        before = committed(rel)
        if before is None:
            failures[rel] = ['regenerated but not committed']
            continue
        after = path.read_text()
        if before == after:
            continue
        if path.suffix == '.md':
            failures[rel] = ['text differs from the committed report']
            continue
        try:
            problems = compare_frames(pd.read_csv(pd.io.common.StringIO(before)), pd.read_csv(path))
        except Exception as error:                       # unparseable is itself a failure
            problems = [f'could not compare: {error}']
        if problems:
            failures[rel] = problems

    tracked = subprocess.run(['git', 'ls-files', 'reports/*.csv', 'reports/*.md'],
                             capture_output=True, text=True, check=True).stdout.split()
    for rel in tracked:
        if not (ROOT / rel).exists():
            failures[rel] = ['committed but no longer produced']

    if failures:
        print('Committed results do not match regenerated output:', file=sys.stderr)
        for rel, problems in sorted(failures.items()):
            print(f'\n  {rel}', file=sys.stderr)
            for problem in problems[:10]:
                print(f'    {problem}', file=sys.stderr)
            if len(problems) > 10:
                print(f'    ... and {len(problems) - 10} more', file=sys.stderr)
        return 1

    print(f'All committed .csv and .md results reproduce '
          f'(text exactly; numbers within {RTOL:g} relative).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
