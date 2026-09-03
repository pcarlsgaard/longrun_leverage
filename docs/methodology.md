# Method and limitations

## Daily return model

For leverage L, underlying total return r, annual expense e, financing spread s,
and calendar-day gap d between consecutive trading closes:

```text
synthetic_return = L * underlying_total_return
                 - (L - 1) * sum(previous-calendar-day DFF / 360)
                 - (L - 1) * spread * calendar_days / 360
                 - annual_expense * calendar_days / 365
wealth[t] = wealth[t-1] * (1 + synthetic_return[t])
```

The funding sum covers each calendar day since the prior close. Interest accrues
over weekends and holidays. Fed funds is a transparent financing proxy; actual
swaps/futures may reference other overnight rates, term rates and dealer spreads.
The model's net borrowing of L−1 represents financed index exposure after
collateral income. A full swap/cash/collateral accounting model is not implemented.

Volatility drag arises from multiplying DAILY returns. No additional decay
deduction is applied. The exact path-compounding gap is
`sum(log(1 + L*r)) - L*sum(log(1+r))`. Funding and fees produce a separate
log-growth gap relative to that frictionless daily leveraged path. For small
returns, the familiar extra volatility penalty relative to L times underlying
log growth is approximately `0.5 * L * (L-1) * variance` per period.

A synthetic daily loss at or below −100% terminates NAV at zero permanently.
This does not model intraday fund termination, exposure reduction, market
closures, counterparty failure, or whether such a product would have survived
historically. Futures limits and liquidity could prevent ideal daily resets.

## Source construction

| Input | Source and treatment |
| --- | --- |
| S&P 500 | Yahoo `^SP500TR` daily total-return index from 1988; earlier VFINX adjusted close, with assumed 0.14% annual expense added back |
| Nasdaq-100 | Nasdaq total-return index `NASDAQXNDX` distributed by FRED from March 1999; earlier Yahoo `^NDX` price index |
| Long Treasuries | TLT adjusted close from July 2002, with assumed 0.15% expense added back; earlier VUSTX adjusted close, with assumed 0.20% expense added back |
| Funding | Federal Reserve DFF via FRED, percent converted to decimal, previous calendar day's rate |
| ProShares actuals | Official daily split-adjusted NAV plus split-adjusted Yahoo distribution events, reinvested on ex-date |
| TMF actuals | Yahoo adjusted market close, reflecting splits and distributions |
| Net 1× fund comparators | Yahoo adjusted closes for SPY, QQQ, TLT, VFINX and VUSTX |

Returns from different sources are spliced, never their differently scaled levels.
The first daily return requiring an unavailable prior primary level uses the
same-date fallback return. No market-price forward-filling is used. Interior
underlying or validation gaps cause failure instead of quietly treating a
multi-day move as one daily leveraged observation.

Current proxy fees are constant assumptions, not recovered fee histories.
Grossed-up ETF returns still retain index tracking error and distribution timing.
Early Nasdaq dividends are unknown in this dataset: zero is a transparent
downward-biased proxy relative to positive reinvested dividends. A +0.5% annual
dividend scenario is included, not presented as estimated historical fact.
VUSTX is a long-Treasury fund, but is not the same as a 20+ year Treasury index.
`treasury_proxy_check.json` quantifies its observed overlap with TLT; the pipeline
does not retroactively force its volatility to match using a fitted beta.

## Fees and calibration

The baseline uses the issuer-page net expense snapshot retrieved September 3,
2026: UPRO 0.89%, SSO 0.87%, TQQQ 0.82%, TMF 0.90%. TMF's quoted net ratio includes
acquired-fund expenses; financing and related swap costs are excluded from the
issuer's operating expense limit. Subtracting a constant full net fee from a
grossed-up TLT proxy is an approximation and need not match actual fund costs.
The historical schedules of fee waivers and benchmark changes have not been
reconstructed. Reported correlations and drift quantify the resulting residual.

The baseline spread is 50 basis points per borrowed dollar per year, with
0/50/100 bp scenarios. It was specified before examining the validation results.
A separate effective residual spread is fitted through December 31, 2018 by
matching training log terminal wealth. Daily leverage stays fixed at 2 or 3.
This residual absorbs fee/proxy/collateral/tracking effects as well as dealer
financing. It is not an identified historical borrowing rate. Applying the fitted
parameter before 2018 is conditional backcasting, not a point-in-time strategy.

2019 onward is held out from calibration. Both baseline and trained results are
reported for training, holdout, and full overlap; no daily residual is added back
to force an exact match. TMF has a different validation basis (market vs NAV),
so its daily tracking-error statistic is not directly comparable to ProShares'.
ProShares' NAV-plus-distributions construction has not been independently
reconciled to official cumulative total-return records at every date.

## Interpretation

All results are nominal USD, reinvested distributions, before investor taxes,
trading costs or inflation. The model supports economic analysis of the
surviving listed strategies; it does not establish long-run suitability.
Historical CAGR tables use each series' own available start; do not rank across
families without first aligning dates. Monthly rolling entry cohorts overlap
and reflect one realized market history, not independent probability estimates.

For 20/30-year cohorts, the entry is an observed month-end close; exit is the
first observed trading close on or after the calendar anniversary. No partial
windows are included. Annual validation tables include partial inception/current
years with actual first/last return dates recorded.

## Primary references

- [UPRO strategy, fees, NAV download](https://www.proshares.com/our-etfs/leveraged-and-inverse/upro)
- [SSO strategy, fees, NAV download](https://www.proshares.com/our-etfs/leveraged-and-inverse/sso)
- [TQQQ strategy, fees, NAV download](https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq)
- [TMF strategy, benchmark and fee exclusions](https://www.direxion.com/product/daily-20-year-treasury-bull-bear-3x-etfs)
- [TLT benchmark and expenses](https://www.ishares.com/uk/professionals/en/products/239454/ishares-20-year-treasury-bond-etf)
- [VUSTX fund profile](https://investor.vanguard.com/investment-products/mutual-funds/profile/vustx)
- [Federal Reserve DFF via FRED](https://fred.stlouisfed.org/series/DFF)
- [Nasdaq-100 total-return data via FRED](https://fred.stlouisfed.org/series/NASDAQXNDX)
- [Nasdaq-100 price-index description](https://fred.stlouisfed.org/series/NASDAQ100)

Machine-readable download URLs and SHA-256 hashes are in `reports/source_manifest.json`.
Source providers retain their respective data rights. Raw vendor payloads are
not republished in this repository.
