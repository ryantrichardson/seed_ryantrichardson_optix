"""For each confirmed PBAR, fetch the relevant ATM option's 1-min aggs over a 10-day window.

Strategy params (configurable):
  ENTRY_DELAY_MIN = 60   # enter 60 min after PBAR
  DTE_TARGET = 21        # weeks ~3 weeks out
  HOLD_DAYS = 10         # exit by trading day +10
  STOP_LOSS_PCT = 75     # exit if drawdown exceeds this
  DIRECTION: fade the wick (down wick -> buy CALL, up wick -> buy PUT)

For each PBAR:
  1. Compute entry datetime (PBAR datetime + 60 min)
  2. Find target expiration: smallest Friday >= entry+21 days
  3. Get underlying price at entry time via S3 minute aggs (or compute from
     trades file)
  4. Find ATM strike for that expiration
  5. Fetch the option's 1-min aggs from S3 over the 10-day window
  6. Save to CSV

Uses Massive API for chain lookups (small, cheap) and S3 for the 1-min aggs.
"""
import os, csv, json, gzip, time, requests
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
from pathlib import Path

import boto3
from botocore.config import Config

API_KEY = os.environ["MASSIVE_API_KEY"]
ACCESS_KEY = os.environ["MASSIVE_S3_ACCESS_KEY_ID"]
SECRET_KEY = os.environ["MASSIVE_S3_SECRET_KEY"]

ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"
BASE = "https://api.massive.com"
ET = ZoneInfo("America/New_York")

# Strategy params
ENTRY_DELAY_MIN = 60
DTE_TARGET = 21
HOLD_DAYS = 10
DTE_TOLERANCE = 7  # accept 14-28 DTE if exact 21 not available

s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "adaptive"}),
)

session = requests.Session()
session.headers.update({"Authorization": f"Bearer {API_KEY}"})


def add_trading_days(d, n):
    """Add n trading days (Mon-Fri) to date d."""
    cur = d
    while n > 0:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n -= 1
    return cur


def find_target_expiration(entry_date, target_dte=DTE_TARGET):
    """Find the next Friday option expiration closest to entry_date + target_dte."""
    target = entry_date + timedelta(days=target_dte)
    # Walk forward from target to next Friday
    while target.weekday() != 4:
        target += timedelta(days=1)
    return target


def load_pbars():
    """Combine confirmed PBARs from both QQQ and SPY scanner output."""
    pbars = []
    # Look in repo-relative path first, then fallback to local workspace
    base = "data/pbar_results"
    if not os.path.isdir(base):
        base = "/home/user/workspace/pbar_results"
    for ticker, fname in [
        ("QQQ", "pbar_qqq_dec2024_apr2025.csv"),
        ("SPY", "pbar_spy_dec2024_apr2025.csv"),
    ]:
        path = f"{base}/{fname}"
        with open(path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # strip CR from windows line endings
                verdict = (row.get("verdict") or "").strip().strip("\r")
                if verdict != "PBAR":
                    continue
                pbars.append({
                    "ticker": ticker,
                    "date": row["date"],
                    "time_et": row["time_et"],
                    "session": row["session"],
                    "direction": row["direction"].strip(),
                    "wick_pct": float(row["wick_pct"]),
                    "extreme": float(row["extreme"]),
                    "close": float(row["close"]),
                })
    return pbars


def find_atm_contract(ticker, underlying_px, target_exp, opt_type):
    """Use Massive API to list contracts for ticker on target_exp, pick the strike
    closest to underlying_px. opt_type is 'call' or 'put'."""
    url = f"{BASE}/v3/reference/options/contracts"
    params = {
        "underlying_ticker": ticker,
        "expiration_date": target_exp.isoformat(),
        "contract_type": opt_type,
        "limit": 100,
    }
    r = session.get(url, params=params, timeout=30)
    r.raise_for_status()
    j = r.json()
    contracts = j.get("results", [])
    if not contracts:
        # Try +/- a few days around the target expiration
        for offset in [-1, 1, -2, 2, -3, 3, 7, -7]:
            alt = target_exp + timedelta(days=offset)
            if alt.weekday() != 4:
                continue
            params["expiration_date"] = alt.isoformat()
            r = session.get(url, params=params, timeout=30)
            r.raise_for_status()
            j = r.json()
            contracts = j.get("results", [])
            if contracts:
                target_exp = alt
                break
    if not contracts:
        return None, None
    # Pick strike closest to underlying
    best = min(contracts, key=lambda c: abs(float(c["strike_price"]) - underlying_px))
    return best, target_exp


def get_underlying_price_at(ticker, dt_et):
    """Quick lookup of underlying close near dt_et via Massive API previous-trade endpoint."""
    # Use the v3 last-trade endpoint with timestamp
    ns = int(dt_et.timestamp() * 1e9)
    url = f"{BASE}/v3/trades/{ticker}"
    params = {"timestamp.gte": ns - 60_000_000_000, "timestamp.lt": ns + 60_000_000_000,
              "limit": 10, "order": "desc"}
    r = session.get(url, params=params, timeout=30)
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        return None
    return float(results[0]["price"])


def fetch_option_minute_aggs(option_ticker, start_date, end_date):
    """Fetch 1-min aggs for an option contract from S3 flat files.

    Each day's flat file contains all options for that day. We need to filter
    to our specific option_ticker (e.g., 'O:QQQ250516C00525000').
    Stream and filter row by row.
    """
    bars = []
    d = start_date
    while d <= end_date:
        if d.weekday() >= 5:
            d += timedelta(days=1)
            continue
        key = f"us_options_opra/minute_aggs_v1/{d.year}/{d.month:02d}/{d.strftime('%Y-%m-%d')}.csv.gz"
        try:
            obj = s3.get_object(Bucket=BUCKET, Key=key)
        except s3.exceptions.NoSuchKey:
            d += timedelta(days=1)
            continue
        except Exception as e:
            print(f"    ! ERROR fetching {key}: {e}")
            d += timedelta(days=1)
            continue

        body = obj["Body"]
        gz = gzip.GzipFile(fileobj=body)
        import io as _io
        text = _io.TextIOWrapper(gz, encoding="utf-8", errors="replace")
        reader = csv.reader(text)
        try:
            header = next(reader)
            t_idx = header.index("ticker")
        except (StopIteration, ValueError):
            d += timedelta(days=1)
            continue
        for row in reader:
            if len(row) <= t_idx:
                continue
            if row[t_idx] == option_ticker:
                bars.append([d.isoformat()] + row)
        try:
            text.close()
        except Exception:
            pass
        d += timedelta(days=1)
    return header if bars else None, bars


def main():
    pbars = load_pbars()
    print(f"=== Pulling option data for {len(pbars)} PBARs ===\n")

    out_dir = Path("/tmp/options_data")
    out_dir.mkdir(exist_ok=True)
    manifest = []

    for i, p in enumerate(pbars, 1):
        ticker = p["ticker"]
        d = date.fromisoformat(p["date"])
        hh, mm = p["time_et"].split(":")
        bar_dt = datetime(d.year, d.month, d.day, int(hh), int(mm), tzinfo=ET)
        # Entry = PBAR time + 60 min (or next morning open if after hours)
        entry_dt = bar_dt + timedelta(minutes=ENTRY_DELAY_MIN)
        # If entry would be outside regular hours, push to next morning 9:30
        if entry_dt.hour >= 16 or entry_dt.hour < 9:
            # next trading day at 9:30
            nd = entry_dt.date()
            if entry_dt.hour >= 16:
                nd += timedelta(days=1)
            while nd.weekday() >= 5:
                nd += timedelta(days=1)
            entry_dt = datetime(nd.year, nd.month, nd.day, 9, 30, tzinfo=ET)
        elif entry_dt.hour == 9 and entry_dt.minute < 30:
            entry_dt = entry_dt.replace(hour=9, minute=30)

        # Strategy: fade the wick
        # Down wick (price spiked low then closed higher) -> expect bounce up -> buy CALL
        # Up wick (price spiked high then closed lower) -> expect drop -> buy PUT
        opt_type = "call" if p["direction"] == "down" else "put"

        target_exp = find_target_expiration(entry_dt.date(), DTE_TARGET)

        # Get underlying price at entry
        try:
            underlying_px = get_underlying_price_at(ticker, entry_dt)
        except Exception as e:
            print(f"  [{i}/{len(pbars)}] {ticker} {p['date']} {p['time_et']} | "
                  f"ERROR getting underlying px: {e}")
            continue
        if underlying_px is None:
            print(f"  [{i}/{len(pbars)}] {ticker} {p['date']} {p['time_et']} | NO UNDERLYING PX")
            continue

        # Find ATM contract
        try:
            contract, actual_exp = find_atm_contract(ticker, underlying_px, target_exp, opt_type)
        except Exception as e:
            print(f"  [{i}/{len(pbars)}] {ticker} {p['date']} {p['time_et']} | "
                  f"ERROR finding contract: {e}")
            continue
        if not contract:
            print(f"  [{i}/{len(pbars)}] {ticker} {p['date']} {p['time_et']} | NO CONTRACT FOUND")
            continue

        opt_tk = contract["ticker"]
        strike = float(contract["strike_price"])
        dte = (actual_exp - entry_dt.date()).days

        # Fetch 1-min aggs from entry_date through entry_date + HOLD_DAYS
        exit_date = add_trading_days(entry_dt.date(), HOLD_DAYS)
        print(f"  [{i}/{len(pbars)}] {ticker} {p['date']} {p['time_et']} {p['direction']:4} "
              f"-> {opt_type.upper()} K={strike} exp={actual_exp} dte={dte} "
              f"underlying=${underlying_px:.2f}")
        print(f"      contract: {opt_tk}, fetching {entry_dt.date()} to {exit_date}...")

        t0 = time.time()
        header, bars = fetch_option_minute_aggs(opt_tk, entry_dt.date(), exit_date)
        elapsed = time.time() - t0

        bar_id = f"{ticker}_{p['date']}_{p['time_et'].replace(':','')}_{p['direction']}"
        out_path = out_dir / f"{bar_id}.csv"
        if bars and header:
            with out_path.open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["data_date"] + header)
                w.writerows(bars)
            print(f"      saved {len(bars):,} bars in {elapsed:.1f}s")
        else:
            print(f"      ! NO BARS FOUND for {opt_tk} (elapsed {elapsed:.1f}s)")

        manifest.append({
            "id": bar_id,
            "ticker": ticker,
            "pbar_date": p["date"],
            "pbar_time_et": p["time_et"],
            "direction": p["direction"],
            "opt_type": opt_type,
            "opt_ticker": opt_tk,
            "strike": strike,
            "expiration": actual_exp.isoformat(),
            "dte_at_entry": dte,
            "entry_dt": entry_dt.isoformat(),
            "exit_date": exit_date.isoformat(),
            "underlying_at_entry": round(underlying_px, 2),
            "wick_pct": p["wick_pct"],
            "extreme": p["extreme"],
            "bars_loaded": len(bars) if bars else 0,
            "out_file": str(out_path),
        })

    # Save manifest
    man_path = out_dir / "manifest.csv"
    if manifest:
        with man_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(manifest[0].keys()))
            w.writeheader()
            w.writerows(manifest)
    print(f"\n=== Done. Wrote {len(manifest)} options datasets. Manifest: {man_path} ===")


if __name__ == "__main__":
    main()
