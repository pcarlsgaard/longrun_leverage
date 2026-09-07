"""Recording exactly what produced a result.

Two rules this module exists to enforce:

- **Hash the inputs and the code, not the wall clock.** A manifest that records
  a timestamp changes on every run and tells you nothing; one that records
  input and source hashes changes exactly when the result could have changed.
- **Hash only what the result actually depends on.** Hashing every file in the
  package makes a manifest churn whenever an unrelated module is added, which
  trains readers to ignore the churn — the opposite of what a manifest is for.
  `source_hashes` walks the real import graph instead.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

__all__ = ['FLOAT_FORMAT', 'ZERO_TOLERANCE', 'sha', 'source_hashes', 'stable_floats']

# Output precision for every committed CSV.
#
# This reduces cross-version churn; it does not eliminate it, and it was a
# mistake to think it could. These results accumulate arithmetic over ~10,000
# trading sessions and different numpy builds order that arithmetic
# differently, so the last digit of a derived statistic moves by ~2e-10
# relative between platforms whatever precision is chosen — cutting further
# would only discard digits that are real. scripts/compare_results.py therefore
# compares numbers within a tolerance rather than byte for byte.
#
# Ten significant figures is well past anything economically meaningful and
# keeps the files readable.
FLOAT_FORMAT = '%.10g'

# Below this, a value is round-off, not a quantity.
#
# Cutting output precision is necessary but not sufficient for byte-reproducible
# results: %g prints its significant digits whatever the exponent, so a
# difference that is algebraically zero prints as -7.993605777e-14 on one
# platform and -8.348877145e-14 on another, and the two disagree in every digit.
# Printing it was misleading anyway — a reader has no way to tell that
# "-7.99e-14" means zero.
#
# Every value in this repository's outputs is either round-off (observed maximum
# ~1e-10) or a real quantity (observed minimum ~1e-6). The gap is five orders
# wide, so this threshold sits in empty space rather than near anything.
ZERO_TOLERANCE = 1e-9


def stable_floats(frame):
    """Snap round-off to exact zero so results are byte-reproducible.

    Apply immediately before writing. A quantity that must be zero should also
    be *asserted* zero in code — this controls only what gets printed, and it
    would happily hide a residual that had grown into a real bug.
    """
    numeric = frame.select_dtypes('number')
    if not len(numeric.columns) if hasattr(numeric, 'columns') else numeric.empty:
        return frame
    snapped = numeric.mask(numeric.abs() < ZERO_TOLERANCE, 0.0)
    if not hasattr(frame, 'columns'):
        return snapped
    out = frame.copy()
    out[numeric.columns] = snapped
    return out


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes(root, module: str) -> dict:
    """SHA-256 of every `letf` module reachable from `module`, including itself.

    The graph is read from the source with `ast`, not from `sys.modules`, so it
    is the same whatever order a run happens to import things in and it still
    catches imports made inside a function body.
    """
    root = Path(root)
    # Callers must pass __spec__.name, not __name__: under `python -m letf.x`
    # the latter is "__main__", which resolves to no file and would silently
    # record an empty manifest.
    if module == '__main__' or '.' not in module:
        raise ValueError(f'Expected a dotted module name, got {module!r} '
                         '(use __spec__.name, not __name__)')
    package = module.split('.')[0]

    def path_of(name):
        return root / f'src/{name.replace(".", "/")}.py'

    seen, stack = set(), [module]
    while stack:
        name = stack.pop()
        source = path_of(name)
        if name in seen or not source.exists():
            continue
        seen.add(name)
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.ImportFrom):
                # level=1 is `from .x import y`; level=0 is absolute.
                base = package if node.level else (node.module or '')
                if node.level and node.module:
                    base = f'{package}.{node.module}'
                if base.split('.')[0] != package:
                    continue
                stack.append(base)
                stack.extend(f'{base}.{alias.name}' for alias in node.names)
            elif isinstance(node, ast.Import):
                stack.extend(a.name for a in node.names if a.name.split('.')[0] == package)

    return {str(path_of(name).relative_to(root)): sha(path_of(name)) for name in sorted(seen)}
