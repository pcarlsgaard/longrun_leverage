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

__all__ = ['FLOAT_FORMAT', 'sha', 'source_hashes']

# Output precision for every committed CSV.
#
# %.12g is one digit past what float64 arithmetic reproduces across numpy and
# pandas versions: counts, minima and maxima stay byte-identical while the 12th
# significant digit of derived statistics moves by ~1e-11 relative. That churn
# is indistinguishable from a real change at a glance, and it defeats
# scripts/check_reproducible.sh. %.10g is inside the stable range.
FLOAT_FORMAT = '%.10g'


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes(root, module: str) -> dict:
    """SHA-256 of every `letf` module reachable from `module`, including itself.

    The graph is read from the source with `ast`, not from `sys.modules`, so it
    is the same whatever order a run happens to import things in and it still
    catches imports made inside a function body.
    """
    root = Path(root)
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
