# Review of the leverage-timing experiments

Scope: all five experiment branches (`agent/portfolio-sma-analysis`,
`agent/sma-falsification`, `agent/capital-reserve`, `agent/regime-signals`,
`agent/price-signal-revision`), reviewed at the cumulative tip `c0f1279`.
The branches form a linear chain, so the tip contains every experiment.
All 52 tests pass, and all eight experiment modules run clean end to end.
Committed results reproduce to ~11 significant figures under pandas 2.2→3.0
and numpy 2.3→2.4 (see C12 for the 12th digit), with the exceptions in C1–C3.

**Verdict: no redesign. The core model is sound. Targeted refactoring is
required, and three of the six experiment modules need to be rerun.**

---

## Status: this review has been acted on

The five experiment branches were consolidated onto one branch and the §4 work
was carried out. What each finding now maps to:

| Finding | Status |
|---|---|
| M1 no null model | `letf/null_model.py` — block permutation matched on switch count, episode lengths and leveraged-day fraction, reported against a Šidák correction for the grid size. Output: `reports/signal_null_model_results.md`. |
| M2 edge concentration | `letf/diagnostics.py`, reported per strategy in the null-model output and in the price-revision report. |
| M3 pseudo-replication | The cross-index report now computes the paired win rate *and* states that it is one comparison re-scored under nuisance parameters, with the subperiod table promoted above it. |
| M4 half-corrected convention | `capital_reserve`, `reserve_cohorts` and `regime_signals` switched to `level_position` and rerun. `regime_signals` also moved its volatility and relative-volatility features onto price returns. |
| M5 ex-post control labelling | Unchanged in substance; the controls were already correct. |
| M6 narrative drift | The two branch-5 reports were rewritten by their generators with calibrated language and explicit pointers to the null model. |
| M7 cohort drawdown state | Unchanged: documented as an entry artifact, not silently altered. |
| C1 empty committed CSV | Regenerated; the CI reproducibility gate now fails on a stale or empty result. |
| C2 hand-edited generated reports | Both generators now emit the full narrative. No committed report is hand-written. |
| C3 orphan deliverables | Both now have generators. |
| C4 ignored `offline` argument | Fixed. |
| C5, C6, C7 duplicate cohort code | One implementation in `letf/cohorts.py`; the other five deleted. The guard and the entry close are now structural. |
| C8 two cost conventions | One convention, stated once in `letf/strategy.py`: bps on half-L1 turnover. The rotation case is turnover 1.0. |
| C9 manifest churn | `letf/provenance.py` hashes the real import graph, read statically from source. |
| C10 untested modules | `tests/test_cohorts.py`, `tests/test_null_model.py`, `tests/test_signals_strategy.py`. |
| C11 slow reserve loop | Unchanged. It is the dominant CI cost but it is correct, and rewriting it would risk the numbers for a runtime gain. |
| C12 output precision | `FLOAT_FORMAT = '%.10g'`, defined once — and see below, because that alone was not enough. |

Three defects were found *during* the remediation, none of which review alone
would have caught:

- `cohorts.nav_path` silently produced a path ending before it started when
  handed a restricted window instead of the full calendar. Now raises.
- The first version of the permutation null could silently return a draw with
  fewer switches than the real rule, when reordering made two same-state
  episodes adjacent and they merged. Replaced with a construction that cannot.
- **C12 was diagnosed incompletely.** Cutting output precision to `%.10g` is
  necessary but not sufficient, and the CI gate on a different Python/numpy
  build is what proved it: `%g` prints its significant digits whatever the
  exponent, so a quantity that is algebraically zero prints as
  `-7.993605777e-14` on one platform and `-8.348877145e-14` on another — no
  agreement in any digit, at any precision setting. Round-off is now snapped to
  exact zero on write (`provenance.stable_floats`, threshold `1e-9`, chosen
  from a five-order gap between the largest observed round-off ~1e-10 and the
  smallest real quantity ~1e-6), and the identities those residual columns were
  silently attesting are now asserted in code, where a tolerance belongs.

The findings below are the original assessment, unchanged.

---

## 1. What holds up

- **Daily-reset economics** (`model.simulate`): ACT/360 financing summed over
  calendar days, ACT/365 fees, absorbing zero NAV, and — correctly — no
  separately deducted "volatility decay". The log decomposition in
  `pipeline.run` and `falsification.economic_components` is an exact identity,
  and `attribution`'s `identity_residual` is algebraically zero by
  construction, so it functions as a live self-check.
- **Look-ahead discipline.** Signals are shifted, warm-up is enforced with
  `min_periods`, and tests explicitly assert no leakage
  (`test_lag1_lag2_both_directions_and_no_leakage`,
  `test_accumulation_lags_and_future_return_invariance`,
  `test_volatility_estimate_is_prior_only_and_additional_lag`). I could not
  find a look-ahead bug in any signal constructor.
- **Provenance.** Hash-pinned frozen input bundles, offline rebuild from a
  clean clone, per-experiment manifests recording runtime versions.
- **`regime_signals.run` validates its reused comparators against the
  committed CSV** and raises if they drift. This is the right pattern and
  should be copied to the other modules.
- **`reports/sma_falsification_results.md` is the strongest document in the
  repo.** Its verdict — "outperformance is not robust under the full battery"
  — is well calibrated and matches what I independently find below.

---

## 2. Methodological findings

### M1. No null model anywhere — the headline effects are not distinguishable from chance

The batteries evaluate ~13,650 rows, but every one of them varies a *nuisance*
parameter (SMA length, lag, spread, switching cost, subperiod). None compares
the result against a distribution of outcomes under "the signal carries no
timing information."

I ran that test: a block permutation that preserves the exact episode-length
distribution, switch count and fraction of leveraged days, and randomizes only
*when* the risk-off episodes fall.

| Strategy | Real CAGR | Always-on | Placebo median | p (uncorrected) |
|---|---:|---:|---:|---:|
| UPRO→SP500 LAG1 | 18.21% | 13.71% | 13.01% | 0.054 |
| UPRO→SP500 LAG2 | 19.09% | 13.71% | 12.77% | 0.026 |
| TQQQ→NDX LAG1 | 22.80% | 13.46% | 13.37% | 0.040 |
| TQQQ→NDX LAG2 | 22.55% | 13.46% | 13.36% | 0.047 |

39.6% of random-timing rules with identical exposure characteristics beat
always-on UPRO — i.e. most of the apparent "benefit" is simply holding less
leverage, which the matched-exposure controls already suspected. The real rule
does sit in the right tail consistently (~95th–97th percentile), so there is a
weak positive signal; but these p-values are *uncorrected* for a search over
648 grid cells × 7 alternative regime signals × the cross-index variants.
Nothing here survives even a mild multiplicity adjustment.

**This is the largest gap in the work**, and it is fixable: the machinery to
run it already exists.

### M2. About 40% of the 40-year edge is a single trading day

For UPRO SMA(200, LAG2) versus always-on:

- Total 40-year log advantage: **1.8439**
- October 1987 alone: **0.7627 — 41.4%**
- **1987-10-19 (Black Monday) alone: 0.7264 — 39.4%**

Excluding October 1987, the advantage falls from +5.37pp to +3.78pp (LAG2) and
+4.50pp to +2.55pp (LAG1).

This also fully explains the headline of the price-only revision. Under LAG2
the legacy total-return signal is still leveraged on 1987-10-19 (position 1.0,
UPRO return −61.5%); the price signal is out (position 0.0). That one day is
the entire "LAG2 CAGR 16.82% → 19.08%" improvement.

In fairness, two committed reports do flag this qualitatively:
`capital_reserve_results.md` ("1987 supplies much of the surviving staged
reserve advantage") and `price_signal_revision_results.md` ("the one-session
earlier 1987 price-index cross is economically material… avoids the extreme
Black Monday timing penalty that dominated the earlier… delay result"). Neither
quantifies it, the SMA and cross-index reports omit it entirely, and — see C2 —
the sentence in the price-signal report is one of the hand-added paragraphs the
generator deletes on the next run.

### M3. Pseudo-replication in the cross-index report

`cross_index_tqqq_sma_results.md` claims the S&P signal beats the Nasdaq signal
in **138/144 (95.8%)** comparisons. I verified the count — but those 144 are
not independent evidence. They are one pair of signal paths that agree on
89.75% of sessions, re-scored under SMA length × spread × lag × cost. The
effective sample is one comparison over one history.

The subperiod table tells the real story (LAG2, 25bp, →Nasdaq):

| Period | Nasdaq signal | S&P signal | Difference |
|---|---:|---:|---:|
| 1987–1999 | 39.41% | 53.52% | +14.11pp |
| 2000–2009 | −15.61% | −16.43% | −0.82pp |
| 2010–2019 | 27.70% | 35.35% | +7.65pp |
| 2020–latest | 43.06% | 32.22% | **−10.84pp** |

Two of four blocks favour it strongly, one is a tie, one reverses hard. The
report's own "Historical subperiod caution" section says this; the "138/144"
framing above it does not.

### M4. The declared correction was applied to only half the modules

`price_signal_revision.py` declares total-return-level SMA signals a
methodological error and switches to `level_position` (price-only). But
`capital_reserve.py`, `reserve_cohorts.py` and `regime_signals.py` still call
`sma_position` (total-return levels):

| Module | Signal constructor | Status |
|---|---|---|
| `analysis.py` | `sma_position` (TR) | superseded |
| `falsification.py` | `sma_position` (TR), price only as comparator | superseded |
| `capital_reserve.py` | `sma_position` (TR) | **needs rerun** |
| `reserve_cohorts.py` | `sma_position` (TR) | **needs rerun** |
| `regime_signals.py` | `sma_position` (TR) | **needs rerun** |
| `price_signal_revision.py` | `level_position` (price) | current |
| `cohort_distributions.py` | `level_position` (price) | current |
| `cross_index_signal.py` | `level_position` (price) | current |

This matters concretely: `regime_signal_results.md` ranks seven signals against
the SMA baseline, and that baseline's LAG2 CAGR is wrong by ~2.3pp for exactly
the Black Monday reason in M2. To its credit the report discloses the mixed
convention ("indicator comparisons are not a pure price-versus-total-return
controlled experiment"), but a disclosed inconsistency is still an
inconsistency once the repo has decided which convention is correct.

### M5. Ex-post controls are correct but unevenly labelled

`regime_signals.matched_control` sets exposure from `state.mean()` over the
full sample; `capital_reserve` matches on `a.effective_equity_exposure.mean()`.
These are legitimate *controls* — the question is whether timing adds anything
beyond average exposure — but they are not implementable strategies.
`capital_reserve_manifest.json` labels the fixed-weight match "Ex-post
diagnostic … not optimized"; the constant-leverage control carries no
equivalent label.

### M6. Narrative drift across the PR stack

The epistemic standard weakens as the chain progresses, while the evidence does
not improve:

- Branch 2: "outperformance is **not robust** under the full battery… a strong
  claim of reliable superiority is **falsified**."
- Branch 4: "the only signals with positive full-window timing value."
- Branch 5: "**particularly attractive**", "138/144", winners bolded in tables.

Because these are stacked PRs, a reader lands on the last one.

### M7. Fresh-investor cohorts inherit market drawdown state

`reserve_cohorts` resets holdings, HWM and recovery state at entry, but passes
`dd` computed from the 1980 all-time high. A cohort entering at −35% fires the
−20% and −30% deployment tranches on its first row. This is defensible (market
drawdown is an observable, not investor state) but contradicts the module
docstring and is an entry artifact for `BAND_DRAWDOWN`, which starts with a 10%
reserve.

---

## 3. Coding defects

| # | Severity | Finding |
|---|---|---|
| C1 | **High** | `reports/price_signal_cohort_percentiles.csv` is committed with a header row and **zero data rows** (commit `43bb503`, "Add cohort percentile output header"), while `price_signal_cohort_distribution_summary.md` presents full percentile tables from it. Regenerating produces 120 rows that match the Markdown exactly — the file was simply committed empty. |
| C2 | **High** | Two committed reports carry hand-written analysis that their own generator overwrites. `cross_index_tqqq_sma_results.md` (63 lines: robustness grid, subperiod caution, cohort tables) is replaced by a 29-line stub from `cross_index_signal.run()`; `price_signal_revision_results.md` loses four interpretation paragraphs — including the only statement of the Black Monday mechanism in M2. CI runs both modules on every push *and* pull request. The repo copies survive only because CI does not commit results back; anyone running the documented command locally destroys them. |
| C3 | Medium | `price_signal_revision_summary.csv` and `price_signal_cohort_distribution_summary.md` have **no generator** in `src/` or CI. Their numbers verify correct today; nothing keeps them correct. |
| C4 | Medium | `falsification.run(root, offline=True)` ignores its own parameter — `load_inputs(root, offline=True)` is hard-coded, so `--offline` reaches only `load_price_signals`. |
| C5 | Medium | `cohort_distributions.cohort_cagrs` omits the `values > 0` guard the other five cohort routines have. A terminated sleeve yields 75/80 NaN cohorts and a NaN quantile row. Latent today (TQQQ bottoms at −99.98%). |
| C6 | Low | Same function omits the prepended entry close that every other cohort routine uses, while its docstring claims "matching existing convention". Harmless for the current window (`ix[0]` and its prior close share a month); silently adds or drops a cohort for any other. |
| C7 | Medium | **Six near-duplicate implementations** of monthly-cohort exact-anniversary CAGR: `pipeline.rolling_outcomes`, `analysis.worst_rolling`, `falsification.rolling_stats`, `capital_reserve.rolling_summary`, `cross_index_signal.cohort_percentiles`, `cohort_distributions.cohort_cagrs`. C5 and C6 exist *because* of this duplication. |
| C8 | Low | Two switching-cost conventions: `falsification.switching_costs` charges bps on the whole portfolio per transition; `reserve.transition_turnover` charges bps on `max(R, R−transfer)`. Each is defensible; cross-module cost comparisons are not apples to apples. |
| C9 | Low | `capital_reserve_manifest.json` hashes every `src/letf/*.py`, including modules it never imports, so it churns whenever unrelated code is added (a rerun added five hashes). |
| C10 | Medium | No tests for `cross_index_signal.py` or `cohort_distributions.py` — the two newest modules, and the ones carrying C1, C5 and C6. |
| C11 | Low | `reserve_cohorts` calls `simulate_reserve`'s pure-Python day loop ~27,000 times (~8 min wall clock, the dominant CI cost). |
| C12 | Low | `float_format='%.12g'` is one digit beyond stable float64 reproducibility. Rerunning under a different numpy/pandas leaves counts, minima and maxima byte-identical but moves the 12th significant digit of derived statistics — 56/112 rows of `capital_reserve_fresh_rolling_summary.csv` churn at ~1e-11 relative. `%.10g` would make the repo byte-reproducible and let a CI regeneration check (see §4.7) work. |

---

## 4. Recommended work

**Rerun, not rewrite (M4).** Switch `capital_reserve.py`, `reserve_cohorts.py`
and `regime_signals.py` to `level_position`, rerun, and either update their
reports or mark the existing ones superseded. Keep `regime_signals`' existing
comparator self-check pattern to detect drift.

**Targeted refactoring:**

1. **`letf/cohorts.py`** — one exact-anniversary cohort/rolling-CAGR
   implementation with the positivity guard and the prepended entry close.
   Delete the other five. Fixes C5, C6, C7.
2. **`letf/signals.py`** — `level_position` as the only SMA constructor;
   deprecate `sma_position` or keep it solely as the legacy audit comparator it
   has become. Fixes M4.
3. **`letf/strategy.py`** — the shared position → returns → costs → metrics
   path currently reimplemented in `analysis.rotations`,
   `falsification.select_returns` and each battery's inner loops. Settle one
   switching-cost convention here (C8).
4. **Generated files are generated.** No committed report should be
   hand-edited if a script writes the same path. Either have the script emit
   the full narrative, or move prose to a separate stable file the script never
   touches. Fixes C2, C3; C1 should be regenerated and recommitted.
5. **`letf/null_model.py`** — block-permutation placebo plus a
   specification-count-adjusted significance column, applied to every headline
   strategy. Fixes M1.
6. **Add an edge-concentration diagnostic** to every strategy row: the share of
   total log advantage contributed by the top 1/5/20 days and by the worst
   drawdown month. Fixes M2, and would have surfaced it automatically.
7. **Tests** for `cross_index_signal` and `cohort_distributions` (C10), and a
   CI step asserting each committed report is byte-identical after
   regeneration — which would have caught C1, C2 and C3. Drop output precision
   to `%.10g` first (C12) so the check is not defeated by last-digit churn.
8. **Collapse the five-PR stack.** All five PRs are open, none merged, `main`
   is five commits behind, and each targets the previous branch. Land them (or
   a squashed equivalent) on `main` so the corrected convention is the default
   a reader sees.

**Reporting:** state the Oct-1987 concentration wherever the SMA advantage is
quoted; replace "138/144" with the subperiod table plus an explicit note that
the comparisons are not independent; align the branch-5 language with the
branch-2 falsification verdict, which the evidence still supports.
