"""Download cached daily source data and retain provenance/hashes."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

SYMBOLS = ["SPY", "QQQ", "TLT", "VFINX", "VUSTX", "UPRO", "SSO", "TQQQ", "TMF", "^SP500TR", "^NDX"]


def yahoo_filename(symbol):
    return f"yahoo_{symbol.lstrip('^')}.json"


def download(root: Path, as_of: str, refresh=False):
    root.mkdir(parents=True, exist_ok=True)
    end = int((pd.Timestamp(as_of, tz="UTC") + pd.Timedelta(days=1)).timestamp())
    jobs = [(yahoo_filename(s), "https://query1.finance.yahoo.com/v8/finance/chart/" +
             urllib.parse.quote(s, safe="") + f"?period1=0&period2={end}&interval=1d&events=div%2Csplits%2CcapitalGains")
            for s in SYMBOLS]
    jobs += [(f"fred_{s}.csv", f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={s}")
             for s in ["DFF", "NASDAQXNDX", "NASDAQ100"]]
    jobs += [(f"nav_{s}.csv", f"https://accounts.profunds.com/etfdata/ByFund/{s}-historical_nav.csv")
             for s in ["UPRO", "SSO", "TQQQ"]]

    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda job: cached_source(root, job, refresh), jobs))
    return records


def cached_source(root, job, refresh=False):
    """Fetch one source using the shared cache, validation and provenance rules."""
    name, url = job
    path = root / name
    if refresh or not path.exists():
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=45) as r:
                    payload = r.read()
                if name.endswith("json"):
                    result = json.loads(payload)["chart"]["result"][0]
                    if len(result.get("timestamp", [])) < 100:
                        raise ValueError("Expected daily history; provider returned too few rows")
                elif not payload.startswith((b"observation_date", b"DATE", b"Date,")):
                    raise ValueError("Unexpected CSV payload")
                path.write_bytes(payload)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
    return {"file": name, "url": url, "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "cached_file_timestamp_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()}


def yahoo(root, symbol, as_of):
    d = json.loads((root / yahoo_filename(symbol)).read_text())["chart"]["result"][0]
    index = pd.to_datetime(d["timestamp"], unit="s", utc=True).tz_convert("America/New_York").tz_localize(None).normalize()
    quote = d["indicators"]["quote"][0]
    adj = d["indicators"].get("adjclose", [{"adjclose": quote["close"]}])[0]["adjclose"]
    frame = pd.DataFrame({"close": quote["close"], "adjclose": adj}, index=index).sort_index()
    if frame.index.duplicated().any():
        raise ValueError(f"Duplicate dates in {symbol}")
    frame = frame.loc[:as_of].dropna()
    if (frame <= 0).any().any():
        raise ValueError(f"Nonpositive levels in {symbol}")
    distributions = pd.Series(0.0, index=frame.index)
    for kind in ["dividends", "capitalGains"]:
        for event in d.get("events", {}).get(kind, {}).values():
            date = pd.Timestamp(event["date"], unit="s", tz="UTC").tz_convert("America/New_York").tz_localize(None).normalize()
            if date in distributions.index:
                distributions.loc[date] += event["amount"]
    frame["distribution"] = distributions
    return frame


def fred(root, symbol, as_of):
    frame = pd.read_csv(root / f"fred_{symbol}.csv", index_col=0, parse_dates=True, na_values=".")
    return pd.to_numeric(frame.iloc[:, 0], errors="coerce").sort_index().loc[:as_of].dropna()


def nav_total_return(root, symbol, market, as_of):
    frame = pd.read_csv(root / f"nav_{symbol}.csv", parse_dates=["Date"]).set_index("Date").sort_index().loc[:as_of]
    # ProShares' downloaded NAV series is already backward split-adjusted.
    # Yahoo distributions are also split-adjusted. Do NOT apply splits again.
    nav = frame["NAV"]
    distribution = market["distribution"].reindex(nav.index, fill_value=0)
    result = (nav + distribution) / nav.shift(1) - 1
    if result.abs().max() > 0.75:
        raise ValueError(f"Unexpected NAV move in {symbol}; inspect split adjustments")
    return result


def build_inputs(raw, config):
    as_of = config["as_of"]
    prices = {s: yahoo(raw, s, as_of) for s in SYMBOLS}
    # A level at the previous close is mandatory. Build a trading calendar from
    # the S&P index plus an older fund, never from weekdays or bond holidays.
    calendar = prices["VFINX"].index.union(prices["^SP500TR"].index).sort_values()
    levels = pd.DataFrame(index=calendar)
    for s in SYMBOLS:
        levels[s] = prices[s]["adjclose"].reindex(calendar)
    returns = levels.pct_change(fill_method=None)
    days = calendar.to_series().diff().dt.days
    provenance = pd.DataFrame(index=calendar)
    underlying = pd.DataFrame(index=calendar)

    def piece(primary, fallback, primary_name, fallback_name):
        # Source returns, rather than differently scaled price levels, are
        # joined. The first primary level uses the fallback's same-day return.
        r = primary.combine_first(fallback)
        source = pd.Series(np.where(primary.notna(), primary_name, fallback_name), index=calendar)
        source.loc[r.isna()] = None
        return r, source

    old_sp = returns["VFINX"] + config["proxy_expenses"]["VFINX"] * days / 365
    underlying["SP500"], provenance["SP500"] = piece(returns["^SP500TR"], old_sp, "SP500TR_index", "VFINX_grossed_proxy")
    ndx = prices["^NDX"]["adjclose"].reindex(calendar).pct_change(fill_method=None)
    xndx = fred(raw, "NASDAQXNDX", as_of).reindex(calendar).pct_change(fill_method=None)
    early = ndx + config["early_nasdaq_dividend_yield"] * days / 365
    underlying["NASDAQ100"], provenance["NASDAQ100"] = piece(xndx, early, "NASDAQXNDX_total_return", "NDX_price_only_proxy")
    for name in ["VUSTX", "TLT"]:
        returns[f"{name}_gross"] = returns[name] + config["proxy_expenses"][name] * days / 365
    underlying["LONG_TREASURY"], provenance["LONG_TREASURY"] = piece(
        returns["TLT_gross"], returns["VUSTX_gross"], "TLT_grossed_proxy", "VUSTX_duration_mismatch_proxy")
    actual = pd.DataFrame(index=calendar)
    for fund in config["funds"]:
        actual[f"{fund}_MARKET"] = returns[fund]
        if fund != "TMF":
            actual[f"{fund}_NAV"] = nav_total_return(raw, fund, prices[fund], as_of).reindex(calendar)
    actual["TMF_PRIMARY"] = actual["TMF_MARKET"]
    for fund in ["UPRO", "SSO", "TQQQ"]:
        actual[f"{fund}_PRIMARY"] = actual[f"{fund}_NAV"]
    # Fail loudly on interior source gaps. Leveraging a multi-day move as if it
    # were one trading day understates volatility drag and is not permissible.
    for name, s in underlying.items():
        valid = s.loc[s.first_valid_index():s.last_valid_index()]
        if valid.isna().any():
            missing = valid.index[valid.isna()].strftime("%Y-%m-%d").tolist()
            raise ValueError(f"Interior gaps in {name}: {missing[:20]}")
    rates = fred(raw, "DFF", as_of) / 100
    return calendar, underlying, provenance, actual, returns, rates
