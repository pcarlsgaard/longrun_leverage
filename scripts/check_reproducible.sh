#!/usr/bin/env bash
# Assert that every committed result under reports/ is what the code produces.
#
# Text — every .md, and every non-numeric cell of every .csv — must match
# exactly. Numbers are compared within a relative tolerance, because byte
# identity is the wrong bar for them: these results accumulate arithmetic over
# ~10,000 trading sessions, different numpy builds order that arithmetic
# differently, and the resulting disagreement (~2e-10 relative) cannot be
# removed by any choice of output precision that also keeps the digits that
# are real. See scripts/compare_results.py.
#
# Manifests are compared on their non-environmental fields: recorded
# Python/numpy/pandas versions differ by design, while a stale input or source
# hash means a manifest is lying about what produced the results beside it.
#
# Excluded: *.png — matplotlib output is not byte-reproducible across platforms
# and font stacks.
#
# A failure here means one of three things, all of which have happened in this
# repository: a committed result was hand-edited after generation, a result was
# committed that no script produces, or a code change silently moved the
# numbers.
set -euo pipefail
cd "$(dirname "$0")/.."

scripts/regenerate.sh

python scripts/compare_results.py

python scripts/compare_manifests.py
