#!/usr/bin/env bash
# Assert that every committed .csv and .md under reports/ is exactly what the
# code produces today.
#
# Manifests are compared too, but only on the fields that are not environmental:
# their recorded Python/numpy/pandas/scipy versions differ by design, while a
# stale input or source hash means the manifest is lying about what produced
# the results beside it.
#
# Excluded, deliberately:
#   *.png — matplotlib output is not byte-reproducible across platforms and
#           font stacks.
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
  # `| head` closes the pipe early; without this, pipefail aborts the script
  # with 141 before the intended exit code is reached.
  git diff -- 'reports/*.csv' 'reports/*.md' | head -200 >&2 || true
  exit 1
fi

if [ -n "$(git status --porcelain --untracked-files=all -- 'reports/*.csv' 'reports/*.md')" ]; then
  echo 'Regeneration produced results that are not committed:' >&2
  git status --porcelain --untracked-files=all -- 'reports/*.csv' 'reports/*.md' >&2
  exit 1
fi

python - <<'CHECK'
import json, subprocess, sys

ENVIRONMENTAL = {'python', 'numpy', 'pandas', 'scipy', 'matplotlib', 'platform'}


def strip(text):
    return {k: v for k, v in json.loads(text).items() if k not in ENVIRONMENTAL}


names = subprocess.run(['git', 'diff', '--name-only', '--', 'reports/*_manifest.json'],
                       capture_output=True, text=True, check=True).stdout.split()
stale = []
for name in names:
    committed = subprocess.run(['git', 'show', f'HEAD:{name}'],
                               capture_output=True, text=True, check=True).stdout
    with open(name) as handle:
        if strip(committed) != strip(handle.read()):
            stale.append(name)
if stale:
    print('Manifests record inputs or sources that no longer produced these results:',
          file=sys.stderr)
    for name in stale:
        print(f'  {name}', file=sys.stderr)
    sys.exit(1)
CHECK

echo 'All committed .csv and .md results reproduce exactly, and every manifest'
echo 'records the inputs and sources that produced them.'
