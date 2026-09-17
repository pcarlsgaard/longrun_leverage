# Longrun · Leverage Lab site

This directory is the static GitHub Pages application for the research simulator.

## Data flow

The browser does not recompute investment simulations. The Pages workflow copies committed research outputs into `site/data/` at deploy time:

- `reports/site_guided_sensitivity.json`
- `reports/leaps_frontier_monte_carlo.csv`
- `reports/leaps_frontier_sensitivities.csv`

That keeps the public site auditable: visible values come from versioned research outputs rather than a separate hidden backend.

## Learning model

The interface follows **Observe → Ask → Test → Explain → Compare**. It intentionally opens sensitivity analysis with economically meaningful questions rather than a wall of tuning controls. The curriculum and source-report mapping live in `reports/site_guided_sensitivity.json`; the full interaction contract is in `docs/site_guided_sensitivity_implementation.md`.

## Deployment

`.github/workflows/pages.yml` validates and deploys `site/` on pushes to `main` that alter the site or its source datasets. The first deployment may require selecting **GitHub Actions** as the Pages publishing source in repository Settings → Pages. After that, relevant commits deploy automatically.

The expected project URL is:

`https://pcarlsgaard.github.io/longrun_leverage/`

## Current scope

The first Pages implementation wires the committed LEAPS frontier, Monte Carlo distributions, option-IV sensitivity, neighboring-budget comparisons, and guided long-range interpretation. The curriculum already describes the broader SMA, always-on leverage, and leveraged stock/bond teaching flows; additional precomputed report tables can be wired into the browser incrementally without changing the site's architecture.
