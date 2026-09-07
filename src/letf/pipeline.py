"""Run: PYTHONPATH=src python -m letf.pipeline [--refresh] [--offline]."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy

from .cohorts import cohort_frame, nav_path
from .data import build_inputs, download
from .model import calendar_days, financing_accrual, simulate, fit_spread, compare, metrics, wealth


def table(frame):
    # Avoid an optional tabulate dependency.
    def fmt(x):
        return f"{x:.4f}" if isinstance(x, (float, np.floating)) else str(x)
    rows = [[fmt(v) for v in row] for row in frame.itertuples(index=False, name=None)]
    return "\n".join(["| " + " | ".join(frame.columns) + " |",
                      "| " + " | ".join(["---"] * len(frame.columns)) + " |"] +
                     ["| " + " | ".join(row) + " |" for row in rows])


def rolling_outcomes(returns, calendar, horizons=(20, 30)):
    """Month-end entry cohorts on exact calendar anniversaries.

    Thin wrapper over :mod:`letf.cohorts`, which owns the convention. Windows
    overlap and are not independent trials.
    """
    frames = []
    for name, r in returns.items():
        nav = nav_path(r, calendar)
        for years in horizons:
            frame = cohort_frame(nav, years)
            frame.insert(0, "series", name)
            frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["series", "horizon_years", "entry_close", "exit_close", "multiple", "cagr"])


def charts(out, sim, actual, config, calendar):
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False})
    colors = {"BASE": "#137c8b", "TRAINED": "#d77c2b", "ACTUAL": "#202a44"}
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    for ax, fund in zip(axes.flat, config["funds"]):
        a = actual[f"{fund}_PRIMARY"].dropna()
        index = a.index
        for variant in ["BASE", "TRAINED"]:
            w = wealth(sim[f"{fund}_{variant}"].loc[index])
            relative = w / wealth(a)
            ax.plot(index, relative, label=variant.lower(), color=colors[variant], lw=1.4)
        ax.axhline(1, color=colors["ACTUAL"], lw=1)
        ax.axvline(pd.Timestamp(config["train_end"]), color="#888888", ls=":")
        ax.set_title(f"{fund}: synthetic / actual wealth")
        ax.set_ylabel("Ratio (1 = exact cumulative match)")
        ax.grid(alpha=.15)
    axes.flat[0].legend(frameon=False)
    fig.suptitle("Tracking accuracy: daily returns compounded from each ETF's first observed return\n"
                 "Vertical line: training ends; subsequent data are held out", fontsize=13)
    fig.savefig(out / "validation.png", dpi=170)
    plt.close(fig)
    fig, axes = plt.subplots(3, 1, figsize=(12, 12), constrained_layout=True)
    families = [("SP500", ["SSO", "UPRO"]), ("NASDAQ100", ["TQQQ"]), ("LONG_TREASURY", ["TMF"])]
    for ax, (under, funds) in zip(axes, families):
        names = [f"{under}_1X"] + [f"{f}_BASE" for f in funds]
        common = sim[names].dropna()
        for name in names:
            ax.plot(common.index, wealth(common[name]), lw=1.4, label=name)
        ax.set_yscale("log")
        ax.set_ylabel("Growth of $1 (log scale)")
        ax.set_title(under.replace("_", " "))
        ax.legend(frameon=False, loc="upper left")
        ax.grid(alpha=.15)
        if under == "NASDAQ100":
            ax.axvspan(common.index[0], pd.Timestamp("1999-03-04"), color="#ddae55", alpha=.12)
        if under == "LONG_TREASURY":
            ax.axvspan(common.index[0], pd.Timestamp("2002-07-30"), color="#ddae55", alpha=.12)
    fig.suptitle("Pure synthetic histories, with daily funding costs and 50 bp financing spread\n"
                 "Shading: early Nasdaq price-only / Treasury duration-mismatched proxy; each panel starts separately", fontsize=13)
    fig.savefig(out / "history.png", dpi=170)
    plt.close(fig)


def run(root, config, refresh=False, offline=False):
    raw, processed, reports = root / "data/raw", root / "data/processed", root / "reports"
    processed.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    existing_manifest = reports / "source_manifest.json"
    if existing_manifest.exists() and not refresh:
        if json.loads(existing_manifest.read_text())["as_of"] != config["as_of"]:
            raise ValueError("as_of changed; use --refresh to fetch data for the new endpoint")
    if offline:
        manifest_path = reports / "source_manifest.json"
        if not manifest_path.exists():
            raise ValueError("Offline mode requires an existing source manifest")
        manifest = json.loads(manifest_path.read_text())
        import hashlib
        for entry in manifest["files"]:
            if hashlib.sha256((raw / entry["file"]).read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError(f"Raw cache hash mismatch: {entry['file']}")
    else:
        manifest = {"as_of": config["as_of"], "files": download(raw, config["as_of"], refresh)}
    manifest["runtime"] = {"python": platform.python_version(), "numpy": np.__version__,
                           "pandas": pd.__version__, "scipy": scipy.__version__, "matplotlib": matplotlib.__version__}
    (reports / "source_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    calendar, underlying, provenance, actual, raw_returns, rates = build_inputs(raw, config)
    sim = underlying.rename(columns={k: f"{k}_1X" for k in underlying})
    diagnostics, fits, coverage, history, decomposition, annual = [], [], [], [], [], []
    costs = pd.DataFrame(index=calendar)
    for fund, spec in config["funds"].items():
        r = underlying[spec["underlying"]].dropna()
        prior = calendar[calendar.get_loc(r.index[0]) - 1]
        days = calendar_days(r.index, prior)
        funding = financing_accrual(r.index, prior, rates)
        a = actual[f"{fund}_PRIMARY"].dropna()
        if not a.index.isin(r.index).all():
            raise ValueError(f"Missing underlying observations during {fund} validation")
        # Also prohibit silently skipping missing actual ETF observations.
        if not a.index.equals(calendar[(calendar >= a.index[0]) & (calendar <= a.index[-1])]):
            raise ValueError(f"Interior gaps in actual {fund} returns")
        train = a.loc[:config["train_end"]].index
        fitted = fit_spread(r.loc[train], a.loc[train], days.loc[train], funding.loc[train],
                            spec["leverage"], spec["expense"])
        fits.append({"fund": fund, "train_first_return": str(train[0].date()), "train_end": str(train[-1].date()),
                     "fitted_effective_spread_bps": fitted * 10000,
                     "base_spread_bps": config["base_financing_spread"] * 10000})
        variants = {"BASE": config["base_financing_spread"], "TRAINED": fitted}
        variants.update({f"SPREAD_{int(s * 10000)}BP": s for s in config["spread_scenarios"]})
        for variant, spread in variants.items():
            s = simulate(r, days, funding, spec["leverage"], spec["expense"], spread)
            sim[f"{fund}_{variant}"] = s
            history.append({"series": f"{fund}_{variant}", "entry_close": str(prior.date()),
                            "first_return": str(r.index[0].date()), "last_return": str(r.index[-1].date()),
                            **metrics(s, days)})
            if variant in {"BASE", "TRAINED"}:
                for period, ix in {"train": train, "holdout": a.loc[pd.Timestamp(config["train_end"]) + pd.Timedelta(days=1):].index,
                                   "full_overlap": a.index}.items():
                    diagnostics.append({"fund": fund, "variant": variant, "period": period,
                                        "actual_basis": "market_adjusted" if fund == "TMF" else "NAV_plus_distributions",
                                        "first_return": str(ix[0].date()), "last_return": str(ix[-1].date()),
                                        "observations": len(ix), **compare(s.loc[ix], a.loc[ix], days.loc[ix])})
        if fund == "TQQQ":
            early = provenance[spec["underlying"]].reindex(r.index).eq("NDX_price_only_proxy")
            augmented = r + early * config["early_nasdaq_dividend_sensitivity"] * days / 365
            sim["TQQQ_EARLY_DIV_50BP"] = simulate(augmented, days, funding, 3, spec["expense"], config["base_financing_spread"])
        base = sim[f"{fund}_BASE"].loc[r.index]
        sim[f"{fund}_HYBRID"] = a.combine_first(base)
        costs[f"{fund}_funding"] = (spec["leverage"] - 1) * funding
        costs[f"{fund}_spread"] = (spec["leverage"] - 1) * config["base_financing_spread"] * days / 360
        costs[f"{fund}_expense"] = spec["expense"] * days / 365
        ideal = simulate(r, days, funding * 0, spec["leverage"], 0, 0)
        # Exact log identity, not a second fee charged by the simulation.
        decomposition.append({"fund": fund, "underlying_log_growth": float(np.log1p(r).sum()),
                              "leverage_times_underlying_log_growth": float(spec["leverage"] * np.log1p(r).sum()),
                              "ideal_leveraged_log_growth": float(np.log1p(ideal).sum()),
                              "path_compounding_gap_log": float((np.log1p(ideal) - spec["leverage"] * np.log1p(r)).sum()),
                              "funding_fees_spread_gap_log": float((np.log1p(base) - np.log1p(ideal)).sum()),
                              "net_synthetic_log_growth": float(np.log1p(base).sum())})
        overlap = pd.DataFrame({"actual": a, "base": base}).dropna()
        for year, group in overlap.groupby(overlap.index.year):
            ar, sr = (1 + group["actual"]).prod() - 1, (1 + group["base"]).prod() - 1
            annual.append({"fund": fund, "year": year, "first_return": str(group.index[0].date()),
                           "last_return": str(group.index[-1].date()), "actual_return": ar,
                           "base_return": sr, "gap_pp": (sr - ar) * 100})
    for name in underlying:
        r = underlying[name].dropna()
        prior = calendar[calendar.get_loc(r.index[0]) - 1]
        history.append({"series": f"{name}_1X", "entry_close": str(prior.date()),
                        "first_return": str(r.index[0].date()), "last_return": str(r.index[-1].date()),
                        **metrics(r, calendar_days(r.index, prior))})
        for source in provenance[name].dropna().unique():
            ix = provenance.index[provenance[name].eq(source)]
            coverage.append({"underlying": name, "source": source, "first_return": str(ix[0].date()),
                             "last_return": str(ix[-1].date()), "observations": len(ix)})
    daily = pd.concat([sim, actual.drop(columns=[c for c in actual if c.endswith("_PRIMARY")]),
                       raw_returns[["SPY", "QQQ", "TLT", "VFINX", "VUSTX"]].add_suffix("_OBSERVED")], axis=1)
    daily.index.name = "date"
    daily.to_csv(processed / "daily_returns.csv", float_format="%.12g")
    levels = pd.DataFrame(index=daily.index)
    for name, r in daily.items():
        r = r.dropna()
        prior = calendar[calendar.get_loc(r.index[0]) - 1]
        levels[name] = pd.concat([pd.Series([1.0], index=[prior]), wealth(r)])
    levels.index.name = "date"
    levels.to_csv(processed / "wealth_indices.csv", float_format="%.12g")
    provenance.to_csv(processed / "daily_source_labels.csv", index_label="date")
    costs.to_csv(processed / "daily_cost_components.csv", index_label="date", float_format="%.12g")
    pd.DataFrame(fits).to_csv(reports / "calibration.csv", index=False)
    validation = pd.DataFrame(diagnostics)
    validation.to_csv(reports / "validation.csv", index=False)
    hist = pd.DataFrame(history)
    hist.to_csv(reports / "historical_metrics.csv", index=False)
    cov = pd.DataFrame(coverage)
    cov.to_csv(reports / "coverage.csv", index=False)
    pd.DataFrame(decomposition).to_csv(reports / "compounding_decomposition.csv", index=False)
    pd.DataFrame(annual).to_csv(reports / "annual_validation.csv", index=False)
    selected = [f"{f}_BASE" for f in config["funds"]] + [f"{u}_1X" for u in underlying]
    rolling = rolling_outcomes(sim[selected], calendar)
    rolling.to_csv(processed / "rolling_20_30_year_cohorts.csv", index=False)
    rolling_summary = rolling.groupby(["series", "horizon_years"]).agg(
        cohorts=("cagr", "size"), min_cagr=("cagr", "min"), median_cagr=("cagr", "median"), max_cagr=("cagr", "max")).reset_index()
    rolling_summary.to_csv(reports / "rolling_summary.csv", index=False)
    # Aggregate check of the older Treasury proxy against TLT in overlap.
    proxy = raw_returns[["VUSTX_gross", "TLT_gross"]].dropna()
    proxy_check = {"start": str(proxy.index[0].date()), "end": str(proxy.index[-1].date()),
                   "daily_correlation": float(proxy.corr().iloc[0, 1]),
                   "TLT_on_VUSTX_return_beta": float(proxy.cov().iloc[0, 1] / proxy["VUSTX_gross"].var()),
                   "meaning": "Descriptive overlap only; no beta adjustment applied to pre-2002 Treasury history."}
    (reports / "treasury_proxy_check.json").write_text(json.dumps(proxy_check, indent=2) + "\n")
    charts(reports, sim, actual, config, calendar)
    full = validation.query("variant == 'BASE' and period == 'full_overlap'")
    holdout = validation.query("variant == 'BASE' and period == 'holdout'")
    cols = ["fund", "daily_correlation", "daily_rmse_bps", "cagr_gap_pp", "terminal_relative_error"]
    report = f"""# Initial reconstruction results

Data through **{config['as_of']}**. This report is generated by `python -m letf.pipeline`.
All returns are nominal USD total returns before investor taxes and trading costs.

## Unfitted baseline vs observed ETF returns

The baseline fixes funding at lagged DFF plus 50 basis points per borrowed dollar, with
constant current expense ratios. No parameter was tuned to these results.
UPRO/SSO/TQQQ use official split-adjusted NAV plus Yahoo distribution events;
TMF uses Yahoo adjusted market close. The NAV construction is a hybrid-source estimate,
not an independently verified official total-return file.

{table(full[cols])}

`cagr_gap_pp` is synthetic minus actual, in annual percentage points.
`terminal_relative_error` is a decimal relative wealth difference, not percentage points.
High daily correlation does not imply negligible long-horizon drift.

## Held-out period, 2019 onward

{table(holdout[cols])}

One fitted effective financing spread per fund is estimated only through 2018.
Its results are in `validation.csv` alongside the unfitted baseline. Training terminal
wealth is matched by construction; it is not an independent accuracy test.
The fitted series remains a conditional historical scenario before the calibration date.

## Underlying coverage

{table(cov)}

The pre-1999 Nasdaq extension omits dividends in the baseline. A 0.5% annual
dividend scenario is also exported; that yield is an assumption, not recovered history.
The pre-2002 Treasury extension uses VUSTX, which has a different duration and
holdings policy from the 20+ year benchmark. Neither extension is a precise historical
reconstruction of the corresponding index. VFINX before 1988 is also a fund proxy.

## Files and interpretation

- `data/processed/daily_returns.csv`: pure simulations, sensitivity scenarios, observed ETFs and explicitly labeled hybrid histories.
- `data/processed/wealth_indices.csv`: each series starts at $1 on its own preceding close; align starts before comparing wealth.
- `data/processed/daily_source_labels.csv`: per-day source/proxy labels.
- `data/processed/daily_cost_components.csv`: funding, spread and expense accruals.
- `data/processed/rolling_20_30_year_cohorts.csv`: monthly entry cohorts with exact anniversary exits.
- `reports/compounding_decomposition.csv`: separates path compounding from funding/fees using an exact log identity.
- `reports/annual_validation.csv`: calendar-year tracking differences, including partial first/last years.

`rolling_summary.csv` is descriptive, with different available entry ranges across assets.
Its overlapping cohorts are not independent samples or future probabilities. Early proxy
limitations carry through all long-horizon results. It is not a ranked portfolio comparison.

The baseline and trained reconstructions remain approximate: fee history, dealer spreads,
benchmark changes, dividend timing, intraday rebalancing and fund closure are not fully
modeled. Survival through the entire historical path is assumed unless a modeled daily
loss reaches 100%, at which point wealth remains zero. Long duration is not a guarantee
of recovery or of outperformance.

![Validation](validation.png)
![Historical paths](history.png)
"""
    (reports / "initial_results.md").write_text(report)
    print(table(full[cols]))
    print(f"\nBuilt {len(daily):,} dated rows and {len(daily.columns)} return series in {processed}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.offline and args.refresh:
        parser.error("--offline and --refresh are mutually exclusive")
    config = json.loads((args.config or args.root / "config.json").read_text())
    run(args.root, config, args.refresh, args.offline)


if __name__ == "__main__":
    main()
