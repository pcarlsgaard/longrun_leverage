"""Prespecified portfolios and lagged SMA rotation; consumes existing daily histories."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import zipfile

import numpy as np
import pandas as pd

from .data import cached_source, fred
from .cohorts import worst_cagr
from .model import calendar_days, matched, metrics, portfolio, wealth
from .signals import sma_position
from .pipeline import rolling_outcomes

CASH = 'TBILL_3M_1X'
PORTFOLIOS = {
    'SP500_100': {'SP500_1X': 1},
    'NASDAQ100_100': {'NASDAQ100_1X': 1},
    'LONG_TREASURY_100': {'LONG_TREASURY_1X': 1},
    'CLASSIC_60_40': {'SP500_1X': .6, 'LONG_TREASURY_1X': .4},
    'SSO_100': {'SSO_BASE': 1}, 'UPRO_100': {'UPRO_BASE': 1},
    'TQQQ_100': {'TQQQ_BASE': 1},
    'SSO60_LT40': {'SSO_BASE': .6, 'LONG_TREASURY_1X': .4},
    'UPRO50_LT50': {'UPRO_BASE': .5, 'LONG_TREASURY_1X': .5},
    'UPRO55_TMF45': {'UPRO_BASE': .55, 'TMF_BASE': .45},
    'UPRO60_TMF40': {'UPRO_BASE': .6, 'TMF_BASE': .4},
    'UPRO40_TMF60': {'UPRO_BASE': .4, 'TMF_BASE': .6},
    'TQQQ50_LT50': {'TQQQ_BASE': .5, 'LONG_TREASURY_1X': .5},
    'TQQQ50_TMF50': {'TQQQ_BASE': .5, 'TMF_BASE': .5},
}
FAMILIES = {'SSO': 'SP500', 'UPRO': 'SP500', 'TQQQ': 'NASDAQ100'}
REGIMES = {'1987': ('1987-01-01', '1987-12-31'),
           '2000_2002': ('2000-01-01', '2002-12-31'),
           '2007_2009': ('2007-01-01', '2009-12-31'),
           '2020': ('2020-01-01', '2020-12-31'),
           '2022': ('2022-01-01', '2022-12-31')}


def tbill_accrual(index, prior_date, discount_rates):
    """DTB3 decimals; assumed 91-day bill; lag one observation-date calendar day.

    Price/face = 1-d*91/360. Daily cash carry = (1/price-1)/91.
    Reinvest carry each calendar day and compound across trading holidays.
    This is a constant-maturity yield-accrual proxy, without mark-to-market.
    """
    calendar_days(index, prior_date)  # validate return calendar
    rates = discount_rates.dropna()
    if not rates.index.is_unique or not rates.index.is_monotonic_increasing:
        raise ValueError('Bill-rate dates must be unique and increasing')
    dates = pd.date_range(prior_date + pd.Timedelta(days=1), index[-1])
    lagged = rates.reindex(dates - pd.Timedelta(days=1), method='ffill')
    if lagged.isna().any():
        raise ValueError('Bill history does not cover the requested period')
    price = 1 - lagged.to_numpy() * 91 / 360
    if (price <= 0).any():
        raise ValueError('Invalid discount bill price')
    carry = (1 / price - 1) / 91
    nav = pd.Series(np.exp(np.log1p(carry).cumsum()), index=dates)
    nav.loc[prior_date] = 1.
    closes = nav.sort_index().reindex(pd.DatetimeIndex([prior_date]).append(index))
    return closes.pct_change(fill_method=None).iloc[1:].rename(CASH)


def path(returns, prior):
    return pd.concat([pd.Series([1.], index=[prior]), wealth(returns)])


def drawdown_details(nav):
    peaks = nav.cummax()
    dd = nav / peaks - 1
    trough = dd.idxmin()
    peak = nav.loc[:trough][nav.loc[:trough] == peaks.loc[trough]].index[-1]
    recovery = nav.loc[trough:][nav.loc[trough:] >= peaks.loc[trough]]
    recovered = recovery.index[0] if len(recovery) else pd.NaT
    # Consecutive high-water marks bound completed underwater episodes.
    # Include the final close for right-censored episodes; a one-session gap
    # between consecutive peaks contains no underwater observation.
    high = np.flatnonzero((dd >= 0).to_numpy())
    bounds = np.r_[high, len(nav)-1] if high[-1] != len(nav)-1 else high
    gaps = np.diff(bounds)
    durations = (nav.index[bounds[1:]] - nav.index[bounds[:-1]]).days.to_numpy()
    active = (gaps > 1) | ((bounds[1:] == len(nav)-1) & (dd.iloc[-1] < 0))
    longest = int(durations[active].max()) if active.any() else 0
    return {'longest_underwater_calendar_days': longest,
            'max_drawdown_peak': str(peak.date()), 'max_drawdown_trough': str(trough.date()),
            'max_drawdown_recovery': '' if pd.isna(recovered) else str(recovered.date()),
            'max_drawdown_recovered': not pd.isna(recovered),
            'max_drawdown_recovery_days_from_trough': np.nan if pd.isna(recovered) else (recovered-trough).days,
            'max_drawdown_recovery_days_from_peak': np.nan if pd.isna(recovered) else (recovered-peak).days}


def worst_rolling(nav, years):
    """Worst daily-entry outcome over the horizon; see :mod:`letf.cohorts`."""
    return worst_cagr(nav, years)


def extended_metrics(r, cash, calendar):
    if not r.index.equals(cash.index) or r.isna().any() or cash.isna().any():
        raise ValueError('Strategy and risk-free calendars must match exactly')
    expected = calendar[(calendar >= r.index[0]) & (calendar <= r.index[-1])]
    if not r.index.equals(expected):
        raise ValueError('Missing trading session')
    prior = calendar[calendar.get_loc(r.index[0]) - 1]
    days = calendar_days(r.index, prior)
    nav = path(r, prior)
    excess = r - cash
    std = excess.std(ddof=1)
    annual = {}
    for year, group in r.groupby(r.index.year):
        # Exclude partial years at either analysis endpoint.
        if prior <= pd.Timestamp(year, 1, 1) and r.index[-1] >= pd.offsets.BDay().rollback(pd.Timestamp(year, 12, 31)):
            annual[year] = (1 + group).prod() - 1
    a = pd.Series(annual, dtype=float)
    return {'entry_close': str(prior.date()), 'first_return': str(r.index[0].date()),
            'last_return': str(r.index[-1].date()), 'observations': len(r),
            **metrics(r, days), 'annualized_volatility': float(r.std(ddof=1)*np.sqrt(252)),
            'sharpe': float(excess.mean()/std*np.sqrt(252)) if std > 0 else np.nan,
            **drawdown_details(nav),
            'worst_calendar_year': int(a.idxmin()) if len(a) else np.nan,
            'worst_calendar_year_return': float(a.min()),
            'best_calendar_year': int(a.idxmax()) if len(a) else np.nan,
            'best_calendar_year_return': float(a.max()),
            **{f'worst_rolling_{y}y_cagr': worst_rolling(nav, y) for y in (1, 5, 10)}}


def state_metrics(r, position, days, off_kind):
    if not r.index.equals(position.index) or position.isna().any():
        raise ValueError('State and return calendars must match')
    p = position.astype(int)
    starts = np.r_[0, np.flatnonzero(np.diff(p.to_numpy()) != 0) + 1]
    ends = np.r_[starts[1:], len(p)]
    lengths = ends - starts
    states = p.iloc[starts].to_numpy()
    switches = len(starts)-1
    out = {'fraction_leveraged': p.mean(), 'fraction_1x': (1-p).mean() if off_kind == '1x' else 0.,
           'fraction_tbill': (1-p).mean() if off_kind == 'tbill' else 0.,
           'switches': switches, 'switches_per_year': switches / (days.sum()/365.25)}
    for state, label in [(1, 'risk_on'), (0, 'risk_off')]:
        mask = p == state
        out[f'average_{label}_episode_sessions'] = float(lengths[states == state].mean()) if (states == state).any() else np.nan
        elapsed = days[mask].sum()
        out[f'{label}_state_cagr'] = float(np.expm1(np.log1p(r[mask]).sum() * 365.25 / elapsed)) if elapsed else np.nan
        out[f'{label}_log_growth_contribution_per_year'] = float(np.log1p(r[mask]).sum() / (days.sum()/365.25))
    return out


def rotations(daily, positions, length, spread, index, funds=FAMILIES):
    results, states = {}, {}
    for fund in funds:
        under = FAMILIES[fund]
        lev = daily.loc[index, f'{fund}_SPREAD_{spread}BP']
        p = positions[(under, length)].loc[index]
        if p.isna().any():
            raise ValueError('SMA warm-up incomplete')
        results[f'{under}_1X'] = daily.loc[index, f'{under}_1X']
        results[f'{fund}_ALWAYS'] = lev
        states[f'{fund}_ALWAYS'] = (pd.Series(1., index=index), 'none')
        for off, label, kind in [(f'{under}_1X', 'NASDAQ' if under == 'NASDAQ100' else under, '1x'),
                                 (CASH, 'TBILL', 'tbill')]:
            name = f'{fund}_SMA_TO_{label}'
            # Never allow buying a synthetic sleeve after it has terminated.
            failed = daily[f'{fund}_SPREAD_{spread}BP'].eq(-1).cumsum().shift(1, fill_value=0)
            if ((failed.loc[index] > 0) & p.eq(1)).any():
                raise ValueError('Cannot enter a terminated leveraged sleeve')
            results[name] = lev.where(p.eq(1), daily.loc[index, off])
            states[name] = (p, kind)
    return pd.DataFrame(results, index=index), states


def cohort_summary(frame, calendar):
    cohorts = rolling_outcomes(frame, calendar)
    return cohorts.groupby(['series', 'horizon_years']).agg(
        cohorts=('cagr', 'size'), first_entry=('entry_close', 'min'), last_entry=('entry_close', 'max'),
        min_cagr=('cagr', 'min'), median_cagr=('cagr', 'median'), max_cagr=('cagr', 'max'),
        min_terminal_multiple=('multiple', 'min'), median_terminal_multiple=('multiple', 'median')).reset_index(), cohorts


def regime_analysis(frame, calendar):
    rows = []
    for name, r in frame.items():
        prior = calendar[calendar.get_loc(r.index[0])-1]
        nav = path(r, prior)
        for regime, (start, end) in REGIMES.items():
            start, end = pd.Timestamp(start), pd.Timestamp(end)
            if prior >= start or r.index[-1] < end:
                continue
            baseline = nav.loc[nav.index < start].index[-1]
            segment = nav.loc[baseline:end]
            detail = drawdown_details(segment)
            trough = pd.Timestamp(detail['max_drawdown_trough'])
            peak_date = pd.Timestamp(detail['max_drawdown_peak'])
            target = nav.loc[peak_date]
            later = nav.loc[trough:]
            recovery = later[later >= target]
            rec = recovery.index[0] if len(recovery) else pd.NaT
            # Separately retain the all-history peak before the regime began.
            previous = nav.loc[:baseline].max()
            after_start = nav.loc[start:]
            regained = after_start[after_start >= previous]
            previous_rec = regained.index[0] if len(regained) else pd.NaT
            rows.append({'series': name, 'regime': regime, 'entry_close': str(baseline.date()),
                         'episode_end': str(segment.index[-1].date()),
                         'episode_max_drawdown': float((segment/segment.cummax()-1).min()),
                         'episode_terminal_multiple': float(segment.iloc[-1]/segment.iloc[0]),
                         'episode_peak': str(peak_date.date()), 'episode_trough': str(trough.date()),
                         'recovery_date': '' if pd.isna(rec) else str(rec.date()),
                         'recovery_days_from_peak': np.nan if pd.isna(rec) else (rec-peak_date).days,
                         'recovery_days_from_trough': np.nan if pd.isna(rec) else (rec-trough).days,
                         'recovered': not pd.isna(rec),
                         'end_relative_to_pre_episode_all_time_peak': float(segment.iloc[-1]/previous),
                         'pre_episode_peak_first_regained': '' if pd.isna(previous_rec) else str(previous_rec.date())})
    return pd.DataFrame(rows)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_inputs(root, offline=False, refresh=False):
    config = json.loads((root/'config.json').read_text())
    manifest_path = root/'reports/portfolio_sma_manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    if manifest and manifest['as_of'] != config['as_of'] and not refresh:
        raise ValueError('as_of changed; rebuild daily histories then use --refresh')
    # The frozen extension bundle permits an analysis-only rebuild in a clean clone.
    bundle = root/'data/snapshots/portfolio_sma_inputs.zip'
    paths = ['data/processed/daily_returns.csv', 'data/raw/fred_DTB3.csv']
    if offline and any(not (root/p).exists() for p in paths):
        if not manifest or sha(bundle) != manifest['input_bundle_sha256']:
            raise ValueError('Missing or unverified analysis input bundle')
        with zipfile.ZipFile(bundle) as z:
            for rel in paths:
                if not (root/rel).exists():
                    (root/rel).parent.mkdir(parents=True, exist_ok=True)
                    (root/rel).write_bytes(z.read(rel))
    daily_path = root/paths[0]
    if not daily_path.exists():
        raise ValueError('Build existing daily histories first, or use --offline with the frozen bundle')
    if offline:
        if not manifest:
            raise ValueError('Offline analysis requires its input manifest')
        for rel in paths + ['config.json']:
            if sha(root/rel) != manifest['inputs_sha256'][rel]:
                raise ValueError(f'Analysis input hash mismatch: {rel}')
    else:
        (root/'data/raw').mkdir(parents=True, exist_ok=True)
        record = cached_source(root/'data/raw', ('fred_DTB3.csv',
                 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3'), refresh)
        sources_path = root/'reports/source_manifest.json'
        sources = json.loads(sources_path.read_text())
        if sources['as_of'] != config['as_of']:
            raise ValueError('Existing series source manifest has a different as_of')
        sources['files'] = [v for v in sources['files'] if v['file'] != record['file']] + [record]
        sources_path.write_text(json.dumps(sources, indent=2)+'\n')
    daily = pd.read_csv(daily_path, index_col='date', parse_dates=True)
    if str(daily.index[-1].date()) != config['as_of']:
        raise ValueError('Daily input endpoint must equal configured as_of')
    # Add cash to an analysis frame; preserve original input files byte for byte.
    rates = fred(root/'data/raw', 'DTB3', config['as_of']) / 100
    daily[CASH] = tbill_accrual(daily.index[1:], daily.index[0], rates)
    if not offline:
        with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED) as z:
            for rel in paths:
                info = zipfile.ZipInfo(rel, (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, (root/rel).read_bytes())
        manifest = {'as_of': config['as_of'],
                    'inputs_sha256': {rel: sha(root/rel) for rel in paths+['config.json']},
                    'input_bundle_sha256': sha(bundle), 'cash_source': record,
                    'cash_convention': 'DTB3 discount basis; 91-day price conversion; previous calendar date observation carried forward; daily reinvestment; no mark-to-market',
                    'runtime': {'python': platform.python_version(), 'numpy': np.__version__, 'pandas': pd.__version__}}
        manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
    return daily, config


def run(root, offline=False, refresh=False):
    daily, config = load_inputs(root, offline, refresh)
    calendar = daily.index
    reports, processed = root/'reports', root/'data/processed'
    positions = {(u, n): sma_position(daily[f'{u}_1X'], calendar, n)
                 for u in set(FAMILIES.values()) for n in (150, 200, 250)}
    required = list(dict.fromkeys(c for weights in PORTFOLIOS.values() for c in weights))
    aligned = matched(pd.concat([daily[required+[CASH]],
                                pd.DataFrame({u: positions[(u, 250)] for u in set(FAMILIES.values())})], axis=1))
    common = aligned.index
    windows = {'common': (common, list(FAMILIES))}
    for u, funds in [('SP500', ['SSO', 'UPRO']), ('NASDAQ100', ['TQQQ'])]:
        ix = matched(pd.concat([daily[[f'{u}_1X', CASH]+[f'{f}_BASE' for f in funds]],
                                positions[(u, 250)].rename('position')], axis=1)).index
        windows[f'{u}_extended'] = (ix, funds)
    static_metrics, static_rolling, static_finance, static_cohorts = [], [], [], []
    sma_metrics, sma_rolling, sma_lengths, sma_finance, sma_cohorts, regimes = [], [], [], [], [], []
    daily_export, state_export = {}, {}
    def evaluate(frame, meta, states=None):
        ix = frame.index
        days = calendar_days(ix, calendar[calendar.get_loc(ix[0])-1])
        rows = []
        for name, r in frame.items():
            row = {**meta, 'series': name, **extended_metrics(r, daily.loc[ix, CASH], calendar)}
            if states and name in states:
                row.update(state_metrics(r, states[name][0], days, states[name][1]))
            rows.append(row)
        summary, cohorts = cohort_summary(frame, calendar)
        for k, v in meta.items():
            summary[k] = v
            cohorts[k] = v
        # Include cohort summaries on sensitivity rows for direct machine-readable comparison.
        wide = summary.pivot(index='series', columns='horizon_years', values=[
            'min_cagr', 'median_cagr', 'max_cagr', 'min_terminal_multiple', 'median_terminal_multiple'])
        wide.columns = [f'cohort_{y}y_{v}' for v, y in wide.columns]
        return pd.DataFrame(rows).merge(wide, on='series', how='left'), summary, cohorts
    for spread in (0, 50, 100):
        for freq in ('quarterly', 'monthly', 'annual'):
            if spread != 50 and freq != 'quarterly':
                continue
            frame = pd.DataFrame(index=common)
            for name, weights in PORTFOLIOS.items():
                w = {c.replace('_BASE', f'_SPREAD_{spread}BP'): v for c, v in weights.items()}
                frame[name] = portfolio(daily.loc[common, list(w)], w, freq)
            meta = {'window': 'common', 'rebalance': freq, 'spread_bps': spread}
            met, summary, cohorts = evaluate(frame, meta)
            if spread == 50:
                static_metrics.append(met); static_rolling.append(summary); static_cohorts.append(cohorts)
            if freq == 'quarterly':
                static_finance.append(met)
            if spread == 50 and freq == 'quarterly':
                primary_static = frame
                regimes.append(regime_analysis(frame, calendar).assign(kind='static', window='common'))
                daily_export.update({f'static__{k}': v for k, v in frame.items()})
        for window, (ix, funds) in windows.items():
            for length in (150, 200, 250):
                frame, states = rotations(daily, positions, length, spread, ix, funds)
                meta = {'window': window, 'sma_days': length, 'spread_bps': spread}
                met, summary, cohorts = evaluate(frame, meta, states)
                sma_finance.append(met)
                if spread == 50:
                    sma_lengths.append(met)
                    if length == 200:
                        sma_metrics.append(met); sma_rolling.append(summary); sma_cohorts.append(cohorts)
                        regimes.append(regime_analysis(frame, calendar).assign(kind='sma', window=window))
                        daily_export.update({f'{window}__{k}': v for k, v in frame.items()})
                        state_export.update({f'{window}__{k}': v[0] for k, v in states.items()})
                        if window == 'common':
                            primary_sma = frame
    outputs = {'portfolio_metrics': static_metrics, 'portfolio_rolling_summary': static_rolling,
               'portfolio_financing_sensitivity': static_finance, 'sma_strategy_metrics': sma_metrics,
               'sma_rolling_summary': sma_rolling, 'sma_length_sensitivity': sma_lengths,
               'sma_financing_sensitivity': sma_finance, 'sma_regime_analysis': regimes}
    for name, rows in outputs.items():
        pd.concat(rows, ignore_index=True).to_csv(reports/f'{name}.csv', index=False, float_format='%.12g')
    for name, rows in [('portfolio', static_cohorts), ('sma', sma_cohorts)]:
        pd.concat(rows, ignore_index=True).to_csv(processed/f'{name}_rolling_cohorts.csv', index=False, float_format='%.12g')
    pd.DataFrame(daily_export).to_csv(processed/'portfolio_sma_daily_returns.csv', index_label='date', float_format='%.12g')
    pd.DataFrame(state_export).to_csv(processed/'sma_daily_positions.csv', index_label='date', float_format='%.12g')
    daily[[CASH]].to_csv(processed/'tbill_daily_returns.csv', index_label='date', float_format='%.12g')
    from .analysis_report import write_report
    write_report(root, config, primary_static, primary_sma, pd.concat(sma_cohorts), calendar)
    print(f'Analysis complete: common entry close {calendar[calendar.get_loc(common[0])-1].date()}, through {common[-1].date()}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--refresh', action='store_true', help='Refresh DTB3 only; never rebuild LETF inputs')
    args = parser.parse_args()
    if args.offline and args.refresh:
        parser.error('--offline and --refresh are mutually exclusive')
    run(args.root, args.offline, args.refresh)


if __name__ == '__main__':
    main()
