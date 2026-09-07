#!/usr/bin/env bash
# Assert that every committed .csv and .md under reports/ is exactly what the
# code produces today.
#
# Excluded, deliberately:
#   *_manifest.json — records the runtime (Python/numpy/pandas versions), so it
#                     differs by environment by design.
#   *.png           — matplotlib output is not byte-reproducible across
#                     platforms and font stacks.
#
# A failure here means one of three things, all of which have happened in this
# repository: a committed result was hand-edited after generation, a result was
# committed that no script produces, or a code change silently moved the
# numbers. All three are bugs.
set -euo pipefail
cd "$(dirname "$0")/.."

scripts/regenerate.sh

if ! git diff --quiet -- 'reports/*.csv' 'reports/*.md'; then
  echo 'Committed results do not match regenerated output:' >&2
  git diff --stat -- 'reports/*.csv' 'reports/*.md' >&2
  echo >&2
  git diff -- 'reports/*.csv' 'reports/*.md' | head -200 >&2
  exit 1
fi

if [ -n "$(git status --porcelain --untracked-files=all -- 'reports/*.csv' 'reports/*.md')" ]; then
  echo 'Regeneration produced results that are not committed:' >&2
  git status --porcelain --untracked-files=all -- 'reports/*.csv' 'reports/*.md' >&2
  exit 1
fi

echo 'All committed .csv and .md results reproduce exactly.'
