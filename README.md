# Long-run leverage

Daily synthetic histories for **UPRO (3× S&P 500), SSO (2× S&P 500),
TQQQ (3× Nasdaq-100), and TMF (3× long US Treasuries)**, with unleveraged
comparators. The purpose is to measure long-horizon outcomes, including daily
compounding, financing costs, drawdowns and entry-date dependence.

**First run: data through September 2, 2026.** See the
[initial results and charts](reports/initial_results.md) and
[methodology](docs/methodology.md). Download the [initial CSV snapshot](data/snapshots/longrun_leverage_2026-09-02.zip).
This is a research reconstruction, not an
assertion that a leveraged ETF is suitable for any particular investor.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -v
longrun-leverage
```

Alternatively, without installing the package: `PYTHONPATH=src python -m letf.pipeline`.
An internet connection is needed for the initial source downloads. The pipeline
caches raw responses and hashes them in `reports/source_manifest.json`.
Use `longrun-leverage --offline` to rebuild from that verified cache. Change
`as_of` in `config.json` and use `--refresh` for a different endpoint. Raw source
data can be revised by their providers; a fresh download need not match an older
snapshot exactly. The initial runtime versions are recorded in the manifest.

The **Build time series** GitHub Actions workflow can also be run manually. It
produces a downloadable artifact with all generated CSVs and reports. Large
raw source files and working generated CSVs are excluded from Git; source code,
parameters, checksums and compact result tables/charts are committed, together
with a compressed initial data snapshot in `data/snapshots/`.

## What is generated

| File | Contents |
| --- | --- |
| `data/processed/daily_returns.csv` | 40 dated return series, in decimal units; missing history remains blank |
| `data/processed/wealth_indices.csv` | $1 wealth indices, including the initial entry close |
| `data/processed/daily_source_labels.csv` | Per-day source and proxy flags |
| `data/processed/daily_cost_components.csv` | Calendar-day financing, spread and fee accruals |
| `data/processed/rolling_20_30_year_cohorts.csv` | Monthly entry cohorts, exact calendar horizons |
| `reports/validation.csv` | Training, held-out and full-overlap tracking diagnostics |
| `reports/annual_validation.csv` | Year-by-year synthetic versus observed returns |
| `reports/compounding_decomposition.csv` | Exact log-growth compounding/cost decomposition |

Suffixes distinguish `BASE` (unfitted 50 bp spread), `TRAINED` (one spread fitted
through 2018), `SPREAD_0BP/50BP/100BP` (funding sensitivity), `NAV` and `MARKET`
(observations), and `HYBRID` (synthetic before actual fund observations, actual
afterward). **Use BASE for the default economic reconstruction.** HYBRID is not
an independent validation series. None of these is a historical tradeable ETF
before its actual inception.

The `1X` comparator columns are gross index returns or grossed-up fund proxies.
Observed `SPY_OBSERVED`, `QQQ_OBSERVED`, and `TLT_OBSERVED` columns retain the funds'
actual net adjusted returns. Nasdaq means **Nasdaq-100**, not Nasdaq Composite.

## Historical coverage

| Family | Initial entry close | Important qualification |
| --- | --- | --- |
| S&P 500 / UPRO / SSO | 1980-01-02 | VFINX proxy through 1988-01-04, then S&P 500 total-return index |
| Nasdaq-100 / TQQQ | 1985-10-01 | Price-only proxy through 1999-03-04; also exports a 0.5% dividend-yield sensitivity |
| Long Treasuries / TMF | 1986-05-19 | VUSTX before TLT coverage in July 2002; different duration/holdings |

## Portfolio and 200-day SMA analysis

See [portfolio and SMA results](reports/portfolio_and_sma_results.md) for the
prespecified 14 static portfolios and SSO/UPRO/TQQQ rotations into either the
corresponding 1× equity index or accrued 3-month Treasury bills. All main tables
use matched dates; quarterly rebalancing and 200 sessions are primary. Monthly /
annual rebalancing, 150/250 sessions, and 0/50/100 bp financing are sensitivities.

```bash
PYTHONPATH=src python -m letf.analysis --offline
# Or after installation:
longrun-leverage-analysis --offline
```

This analysis-only command reuses the original daily histories and does not
rebuild underlying series. A verified, self-contained input bundle is committed
at `data/snapshots/portfolio_sma_inputs.zip`, enabling offline analysis from a
clean clone. It contains the unchanged daily-return CSV and cached FRED DTB3.
Missing inputs are restored; existing inputs must match recorded hashes.
The original full-series pipeline still requires its full raw cache offline.

To update, first regenerate daily histories with the existing pipeline and
updated configuration, then run `longrun-leverage-analysis --refresh` to refresh
DTB3 and archive the analysis inputs. The analysis adds cash provenance to
`reports/source_manifest.json` and its own input/configuration/bundle hashes to
`reports/portfolio_sma_manifest.json`.

Compact metrics, rolling summaries, regime comparisons and sensitivities are
under `reports/`. Daily strategy returns, positions, T-bill returns and exact
individual cohorts are written to `data/processed/`. The report documents all
metric conventions and limitations, including early index proxies and the
idealized close-to-close execution assumption.

## Combining funds

The portfolio helper supports fixed initial weights with no further rebalancing,
or monthly, quarterly and annual rebalancing. ETF daily leverage resets occur
inside each sleeve regardless of the portfolio's rebalance rule.

```python
import pandas as pd
from letf.model import portfolio, wealth

daily = pd.read_csv("data/processed/daily_returns.csv", index_col="date", parse_dates=True)
# Illustrative weights only; no portfolio optimization has been performed.
legs = daily[["UPRO_BASE", "TMF_BASE"]]
start = max(legs[col].first_valid_index() for col in legs)
legs = legs.loc[start:]  # preserve any interior missing rows so validation catches them
result = portfolio(legs, {"UPRO_BASE": 0.55, "TMF_BASE": 0.45}, rebalance="quarterly")
growth = wealth(result, initial=10_000)
```

Compare portfolios over identical dates. A later analysis can test matched
20–30-year entry cohorts, contributions, rebalancing policies and real returns.
Longer holding periods do not remove financing costs or recover money lost by
a fund that reaches zero. The current simulator includes no investor taxes,
transaction costs, new contributions, withdrawals or inflation adjustment.
