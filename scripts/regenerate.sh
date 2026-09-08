#!/usr/bin/env bash
# Regenerate committed results from the frozen input bundles.
#
# By default only the steps that need it: `scripts/stale_modules.py` decides,
# per step, whether the manifest beside its results still proves them — same
# source files, same import graph, same inputs, same committed outputs. A step
# that fails any of those runs and is compared as before. Nothing is taken on
# trust; see that script for what "skipped" actually asserts.
#
# Pass --full to rebuild everything regardless. That is the right thing to do
# when you want the guarantee re-established from nothing rather than from the
# manifests, and it is what the scheduled CI job runs.
#
# If running this leaves `git status` dirty, the committed results and the code
# that claims to produce them have diverged — see scripts/check_reproducible.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src

STALE=$(python scripts/stale_modules.py ${1:-})
if [ -z "${STALE// }" ]; then
    echo 'Every step is already proven by its manifest; nothing to regenerate.'
    exit 0
fi
echo "Regenerating: ${STALE}"

needs() { case " ${STALE} " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

if needs analysis;              then longrun-leverage-analysis --offline; fi
if needs falsification;         then longrun-leverage-falsification --offline; fi
if needs reserve;               then longrun-leverage-reserve --offline; fi
if needs reserve-cohorts;       then python -m letf.reserve_cohorts; fi
if needs regime-signals;        then python -m letf.regime_signals; fi
if needs price-signal-revision; then python -m letf.price_signal_revision; fi
if needs cohort-distributions;  then python -m letf.cohort_distributions; fi
if needs cross-index-signal;    then python -m letf.cross_index_signal; fi
if needs null-model;            then python -m letf.null_model; fi
if needs hedge-alternatives;    then python -m letf.hedge_alternatives; fi
if needs leaps-robustness;      then python -m letf.leaps_robustness; fi
if needs treasury-leverage;     then python -m letf.treasury_leverage; fi
if needs lflr-reproduction;     then python -m letf.lflr_reproduction; fi
if needs nasdaq-leaps;          then python -m letf.nasdaq_leaps; fi
if needs leaps-frontier;        then python -m letf.leaps_frontier; fi
if needs xnd-short-maturity;    then python -m letf.xnd_short_maturity; fi
if needs leaps-duration;        then python -m letf.leaps_duration; fi
