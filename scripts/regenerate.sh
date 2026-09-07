#!/usr/bin/env bash
# Regenerate every committed result from the frozen input bundles.
#
# This is the single documented way to rebuild reports/. If running it leaves
# `git status` dirty, the committed results and the code that claims to produce
# them have diverged — see scripts/check_reproducible.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src

longrun-leverage-analysis --offline
longrun-leverage-falsification --offline
longrun-leverage-reserve --offline
python -m letf.reserve_cohorts
python -m letf.regime_signals
python -m letf.price_signal_revision
python -m letf.cohort_distributions
python -m letf.cross_index_signal
python -m letf.null_model
