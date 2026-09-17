# Guided sensitivity learning layer

## Goal

Turn the simulation site from a result browser into an exploratory learning environment that directs the learner toward the right economic questions without removing freedom to explore.

The core loop is:

**Observe → Ask → Test → Explain → Compare**

Every major chart should help the learner notice something, every observation should suggest a useful question, every question should have a direct experiment, and every experiment should end with an economic explanation rather than only updated numbers.

The machine-readable curriculum lives in `reports/site_guided_sensitivity.json`.

## 1. Navigation and state

Add **Sensitivity** as a first-class analysis mode beside Timeline, Monte Carlo, and Frontier.

The selected strategy must remain synchronized across all four modes. Clicking a point in a frontier or Pareto plot should populate the same selected-strategy state used by Timeline and Sensitivity. Likewise, a strategy chosen in Sensitivity should be addable to the shared timeline and comparison set without reselecting it.

On mobile, the teaching panel should collapse below the active chart rather than becoming a narrow right rail.

## 2. Persistent strategy thesis card

For the currently selected strategy show a compact card before any sensitivity controls:

- **Why this strategy can work** — one or two sentences describing the economic mechanism.
- **What it is implicitly betting on** — two or three assumptions.
- **What it does not require** — helps prevent mistaken mental models.
- **What can break it** — the primary failure modes.
- **Most decision-relevant uncertainty** — link directly to the corresponding experiment.

Do not present these as investment recommendations. They are explanatory descriptions of the modeled strategy.

## 3. Questions worth asking

The default Sensitivity view should show 3–5 cards sourced from `site_guided_sensitivity.json`, ordered by economic importance rather than ease of computation.

Each card contains:

1. the question,
2. a one-line reason it matters,
3. a **Test this** button.

Do not show a wall of sliders until the learner chooses a question.

Example for SPX 85/40:

> **How expensive can long-dated options become before this strategy loses its advantage?**
>
> Long-dated option pricing is a first-order uncertainty for this structure.
>
> `Test this →`

## 4. Experiment view

After the learner chooses a question, expose only the controls needed to answer it.

The experiment result must always have three sections:

### What changed?

Show the quantitative deltas in the same units used elsewhere on the site. Prefer 3–5 metrics rather than a full results table. Examples:

- median CAGR,
- p5 CAGR,
- median max drawdown,
- P(max DD > 60%),
- mean delta exposure.

### Why did it change?

Give a mechanism-level explanation. Examples:

- Higher IV at a fixed LEAPS premium budget buys fewer contracts and changes effective delta.
- Shorter maturity can appear superior because the same premium budget buys more exposure.
- Moving from 35% to 45% option budget mostly changes equity exposure and tail risk; it is not automatically a structural efficiency gain.
- SMA risk-off to 1x can preserve equity compounding while still reducing leverage during adverse regimes.

### What should I ask next?

Present one next experiment selected from the curriculum. Prefer a falsification or normalization test over a nearby parameter tweak.

Example:

> **Shorter calls appear to outperform.** Mean delta also rose from 1.01 to 1.14. Before concluding that short maturity is superior, match exposure and retest.
>
> `Match exposure and retest →`

## 5. Sensitivity hierarchy

Display sensitivities in three groups:

### First-order

Can change whether the strategy is attractive at all.

### Second-order

Meaningfully changes risk/return but usually leaves the central thesis intact.

### Implementation

Operational choices whose measured differences are often smaller than major model uncertainties.

Do not assign arbitrary 1–10 importance scores. Base ordering on the actual experiments and evidence in the repository.

### Initial classification

**LEAPS**
- First-order: option IV/skew; underlying growth.
- Second-order: premium budget/delta; realized volatility; Treasury behavior.
- Implementation: maturity; roll interval; modest spread differences.

**SMA rotation**
- First-order: whether trend state identifies adverse leverage regimes; crash/reversal path.
- Second-order: SMA duration; execution lag; whipsaw frequency.
- Implementation: modest switching costs and small changes inside the broad 150–250 day robustness region.

**Always-on leverage**
- First-order: underlying return; realized volatility.
- Second-order: financing cost; return sequence.
- Implementation: small fee differences.

**Leveraged stock/bond portfolios**
- First-order: equity/bond correlation and inflation/rate regime.
- Second-order: duration; financing; rebalancing path.
- Implementation: modest weight changes.

## 6. Discovery prompts

The site should recognize common patterns and explicitly teach the learner what they imply.

### Apparent improvement caused by exposure

Trigger when a candidate has higher return and materially higher delta/gross exposure.

Show:

> **Return improved, but exposure also increased.** Is this a better structure or simply more risk?

Offer `Match exposure and retest`.

### Nearby parameter differences below model uncertainty

Trigger when the measured difference is below the relevant modeling error bar—for example, a duration CAGR difference smaller than the effect of one volatility point in option pricing.

Show:

> **Below model resolution.** This ranking is too small relative to option-pricing uncertainty to justify further tuning.

Offer the larger sensitivity instead, usually IV/skew.

### Concentrated timing advantage

For trend strategies, when a large share of relative wealth comes from a handful of crash sessions:

> **The advantage is concentrated.** Test whether this is a robust regime effect or dependence on a few historical paths.

Offer window robustness, alternate risk-off state, execution lag, or fast-reversal stress.

### Risk-off destination result

When UPRO→1x outperforms UPRO→cash over the relevant long horizon:

> **The moving average may be timing leverage rather than equity ownership.**

Offer a direct risk-off destination comparison.

## 7. Frontier integration

The frontier should become a teaching surface rather than a passive plot.

On point selection:

1. Show whether the point is non-dominated in the current x/y view.
2. Show the closest lower-risk and higher-risk neighbors.
3. Explain the incremental trade:
   - delta median CAGR,
   - delta p5 CAGR,
   - delta P(DD > 60%),
   - delta exposure/premium budget.
4. Offer a question appropriate to the movement.

Example:

> **SPX 85/40 → SPX 85/45**
>
> Median CAGR rises, while the lower tail and severe-drawdown probability worsen. The premium budget and mean delta both rise. This is primarily a move along the risk frontier, not evidence that 85/45 is a more efficient option structure.

For NDX vs SPX comparisons, prompt the learner to distinguish:

- underlying growth difference,
- option pricing difference,
- effective leverage difference.

## 8. Long-range assessment redesign

Retain the existing Long-range Assessment and values tables, but precede them with a compact interpretation layer.

For each selected strategy show:

- **Central thesis**
- **Strongest evidence**
- **Best falsification attempted**
- **Most important unresolved uncertainty**
- **Parameter choices that matter less than they appear**

The values table remains available immediately below this layer for auditability.

## 9. Challenge prompts

Optional challenge prompts can be shown after an experiment. Keep them analytical rather than gamified.

Examples:

> You raised the LEAPS premium budget from 35% to 45% and CAGR rose. Did efficiency improve?

Correct concept: not necessarily; inspect exposure and the lower tail.

> A 15-month call beat a 24-month call at the same premium budget. What should you check first?

Correct concept: delta/exposure per premium dollar.

> A 200-day SMA backtest works. What is stronger evidence: the exact 200-day optimum, or similar results across 150–250 days?

Correct concept: the broad robustness plateau.

Challenges should always resolve by opening the relevant experiment rather than simply declaring an answer.

## 10. Data and evidence hooks

Use existing result files rather than recomputing simulations in the browser whenever possible.

Initial sources:

- `reports/leaps_frontier_results.md`
- `reports/leaps_frontier_historical.csv`
- `reports/leaps_frontier_monte_carlo.csv`
- `reports/leaps_frontier_sensitivities.csv`
- `reports/leaps_duration_roll_results.md`
- `reports/xnd_short_maturity_results.md`
- `reports/leaps_treasury_leverage_results.md`
- `reports/price_signal_revision_results.md`
- `reports/sma_falsification_results.md`
- `reports/signal_null_model_results.md`
- `reports/regime_signal_results.md`
- `reports/hedge_alternatives_results.md`
- `reports/compounding_decomposition.csv`

When a requested experiment is not precomputed, disable the action with a brief explanation rather than implying that a result exists.

## 11. Acceptance criteria

The implementation is complete when:

- Clicking any strategy in Timeline, Frontier, or Monte Carlo updates the same Sensitivity state.
- Sensitivity opens with questions, not controls.
- LEAPS, SMA rotation, always-on leverage, and stock/bond leverage each have distinct questions and failure modes.
- At least one LEAPS flow supports fixed-budget → matched-delta retesting.
- At least one SMA flow compares leverage-to-1x vs leverage-to-cash.
- Experiment results always provide **What changed / Why / What next**.
- A model-resolution stop rule is displayed when a parameter ranking is smaller than the relevant uncertainty bar.
- The existing detailed values tables remain accessible and unchanged for auditability.
- Mobile layout remains usable without horizontal scrolling for the teaching panel.
- No UI language presents a modeled strategy as a personal investment recommendation.

## 12. Design principle

The learner should be free to wander, but the interface should continually make the highest-value next question easier to ask than a low-value parameter tweak.

The intended outcome is not that the learner memorizes which strategy had the highest backtest CAGR. It is that they learn to distinguish:

- return from exposure vs return from structural efficiency,
- strategy uncertainty vs model uncertainty,
- robust mechanisms vs parameter optima,
- diversification by capital weight vs risk contribution,
- historical success vs an assumption that still needs to hold.
