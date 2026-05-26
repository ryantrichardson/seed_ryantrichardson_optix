#!/usr/bin/env python3
"""
Layer market-regime context onto every wick:

  - VIX level + 5-day change (fear regime)
  - SKEW level (tail-risk pricing)
  - VVIX level (vol-of-vol; institutional uncertainty)
  - Sector index 5-min return at wick time (per-ticker mapping)

Reads:  data/all_wicks_for_enrichment.json
Writes: data/all_wicks_regime.csv  + data/all_wicks_regime.json
"""
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests

API_KEY = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
ET = timezone(timedelta(hours=-4))

PILOT_FILE = Path(os.environ.get("PILOT_FILE", "data/all_wicks_for_enrichment.json"))
OUT_CSV = Path(os.environ.get("OUT_CSV", "data/all_wicks_regime.csv"))
OUT_JSON = Path(os.environ.get("OUT_JSON", "data/all_wicks_regime.json"))

# Which sector index represents the macro context for each ticker
SECTOR_MAP = {
    "AMD": "I:SOX",      # PHLX semis
    "NVDA": "I:SOX",
    "INTU": "I:NDX",
    "MSFT": "I:NDX",
    "TSLA": "I:NDX",     # mega-cap tech
    "QQQ": "I:NDX",      # itself a Nasdaq-100 proxy; still useful
    "PLTR": "I:NDX",     # most aligned available
    "SHOP": "I:NDX",
    "SLV": "I:SPX",      # silver doesn't map well to equity indices; SPX as fallback
    "OWL": "I:SPX",
    "PYPL": "I:NDX",
    "SOXL": "I:SOX",
    "RTX": "I:SPX",
    "NEM": "I:SPX",
}


def get(path, params=None):
    """GET helper with retry."""
    params = dict(params or {})
    params["apiKey"] = API_KEY
    url = f"{BASE}{path}?{urlencode(params)}"
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                print(f"  [warn] {path}: {e}", file=sys.stderr)
                return None
            time.sleep(1 + attempt)
    return None


def index_value_at(index_ticker: str, dt_iso: str):
    """Return (close at wick minute, close 5 days prior). Uses 1-min bars.
    For 5-day prior we use the daily close 5 trading days back."""
    wick_dt = datetime.fromisoformat(dt_iso)
    # 1-min bar at wick minute: query a +/-1 min window
    minute_ms = int(wick_dt.timestamp() * 1000)
    win_lo = minute_ms - 120_000
    win_hi = minute_ms + 120_000
    data = get(
        f"/v2/aggs/ticker/{index_ticker}/range/1/minute/{win_lo}/{win_hi}",
        {"adjusted": "true", "sort": "asc", "limit": 10},
    )
    val_at = None
    if data and data.get("results"):
        # Pick the bar whose start <= wick_minute
        for bar in data["results"]:
            if bar["t"] <= minute_ms:
                val_at = bar["c"]
            else:
                break
        if val_at is None and data["results"]:
            val_at = data["results"][0]["c"]
    return val_at


def index_value_5min_change(index_ticker: str, dt_iso: str):
    """Index change over the 5 minutes ending at the wick minute, in %."""
    wick_dt = datetime.fromisoformat(dt_iso)
    minute_ms = int(wick_dt.timestamp() * 1000)
    win_lo = minute_ms - 360_000   # 6 min back
    win_hi = minute_ms + 60_000
    data = get(
        f"/v2/aggs/ticker/{index_ticker}/range/1/minute/{win_lo}/{win_hi}",
        {"adjusted": "true", "sort": "asc", "limit": 20},
    )
    if not data or not data.get("results"):
        return None, None
    bars = data["results"]
    start = bars[0]["c"]
    end = None
    for b in bars:
        if b["t"] <= minute_ms:
            end = b["c"]
    if end is None:
        end = bars[-1]["c"]
    if not start:
        return None, None
    return end, round((end - start) / start * 100.0, 4)


def index_daily_change(index_ticker: str, wick_date: str, lookback_days: int):
    """Daily close on wick date and on (wick date - lookback) trading days.
    Returns (close_at_wick_date, pct_change_from_lookback)."""
    end = datetime.strptime(wick_date, "%Y-%m-%d").date()
    start = end - timedelta(days=lookback_days + 10)
    data = get(
        f"/v2/aggs/ticker/{index_ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        {"adjusted": "true", "sort": "asc", "limit": 60},
    )
    if not data or not data.get("results"):
        return None, None
    bars = data["results"]
    # Find wick-date bar
    end_close = None
    for b in bars:
        d = datetime.fromtimestamp(b["t"] / 1000, tz=timezone.utc).date()
        if d.isoformat() == wick_date:
            end_close = b["c"]
            break
    if end_close is None and bars:
        end_close = bars[-1]["c"]
    # 5 trading days back = bars[-6] when ordered asc and wick_date is last
    if len(bars) >= lookback_days + 1:
        start_close = bars[-(lookback_days + 1)]["c"]
        pct = round((end_close - start_close) / start_close * 100.0, 4)
        return end_close, pct
    return end_close, None


def enrich_one(w: dict) -> dict:
    ticker = w["ticker"]
    dt_iso = w["datetime"]
    wick_date = w["date"]
    print(f"\n=== {ticker} {wick_date} {w['time_et']} ET  ({w['direction']} {w['wick_pct']}%) ===")

    # VIX level + 5-day change (using daily bars to avoid index minute-bar gaps)
    vix_level, vix_5d = index_daily_change("I:VIX", wick_date, 5)
    skew_level, _ = index_daily_change("I:SKEW", wick_date, 5)
    vvix_level, _ = index_daily_change("I:VVIX", wick_date, 5)

    # Sector index at wick time + its 5-min change
    sector = SECTOR_MAP.get(ticker, "I:SPX")
    sec_val, sec_5min = index_value_5min_change(sector, dt_iso)
    # If the sector index doesn't produce minute bars (some indices are EOD-only),
    # fall back to daily change for that day.
    if sec_val is None or sec_5min is None:
        sec_val, sec_daily = index_daily_change(sector, wick_date, 1)
        sec_5min = sec_daily

    enriched = dict(w)
    enriched["regime"] = {
        "vix_level": vix_level,
        "vix_5d_pct_change": vix_5d,
        "skew_level": skew_level,
        "vvix_level": vvix_level,
        "sector_index": sector,
        "sector_value": sec_val,
        "sector_5min_pct_change": sec_5min,
    }
    print(f"  VIX={vix_level} (5d {vix_5d}%)  SKEW={skew_level}  VVIX={vvix_level}")
    print(f"  {sector}={sec_val}  5min={sec_5min}%")
    return enriched


def main():
    wicks = json.loads(PILOT_FILE.read_text())
    print(f"Loaded {len(wicks)} wicks from {PILOT_FILE}")
    out = [enrich_one(w) for w in wicks]
    OUT_JSON.write_text(json.dumps(out, indent=2))

    cols = [
        "ticker", "date", "time_et", "direction", "wick_pct", "ratio",
        "touched", "days_to_touch",
        "vix_level", "vix_5d_pct", "skew_level", "vvix_level",
        "sector_index", "sector_5min_pct",
    ]
    with OUT_CSV.open("w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(cols)
        for r in out:
            reg = r.get("regime") or {}
            wr.writerow([
                r["ticker"], r["date"], r["time_et"], r["direction"],
                r["wick_pct"], r["ratio"], r["touched"], r.get("days_to_touch", ""),
                reg.get("vix_level"), reg.get("vix_5d_pct_change"),
                reg.get("skew_level"), reg.get("vvix_level"),
                reg.get("sector_index"), reg.get("sector_5min_pct_change"),
            ])
    print(f"\nWrote {OUT_JSON} and {OUT_CSV}")


if __name__ == "__main__":
    main()
