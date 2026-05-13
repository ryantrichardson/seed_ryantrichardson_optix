"""
Update Short Volume Ratio (SVR) data for the watchlist.

Uses Massive's /stocks/v1/short-volume endpoint, which IS included on Ryan's
current plan (unlike /v3/trades, which would have powered DPR but requires
Stocks Advanced).

SVR captures a similar "who is selling into this market" signal as DPR:
when short volume ratio rises into a rally, it means short-sellers are
pressing into strength - often a contrarian setup. When SVR drops while
price rises, shorts are capitulating / not fighting it - usually bullish
continuation.

Outputs per ticker, written to data/:
  {T}_SVR.csv               Pine Seeds-style CSV (kept for parity w/ Optix)
  {T}_SVR_TS.txt            TradeStation ASCII: short_volume_ratio (0-100)
  {T}_SVR_NASDAQ_TS.txt     TradeStation ASCII: % of short volume routed
                            through NASDAQ Carteret (FINRA TRF) - this is
                            the closest proxy we have on this plan to the
                            dark-pool signal we originally wanted
  {T}_SVR_components.csv    full daily breakdown w/ all venue splits
  {T}_SVR_debug.json        latest values + how to read them
"""

import csv
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qsl

import requests

API_KEY = os.environ["MASSIVE_API_KEY"]
BASE_URL = "https://api.massive.com"

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

BACKFILL_DAYS = 365
PAGE_LIMIT = 1000
REQUEST_SLEEP = 0.12


def get_json(path, params=None):
    p = dict(params or {})
    p["apiKey"] = API_KEY
    r = requests.get(f"{BASE_URL}{path}", params=p, timeout=60)
    print(f"GET {r.url.replace(API_KEY, '***')} -> {r.status_code}")
    if REQUEST_SLEEP:
        time.sleep(REQUEST_SLEEP)
    r.raise_for_status()
    return r.json()


def read_tickers():
    with (ROOT / "tickers.csv").open() as f:
        return [row["ticker"].strip().upper()
                for row in csv.DictReader(f) if row.get("ticker")]


def fetch_short_volume(ticker, start, end):
    """Paginate the short-volume endpoint for the date window."""
    rows = []
    path = "/stocks/v1/short-volume"
    params = {
        "ticker": ticker,
        "date.gte": start,
        "date.lte": end,
        "order": "asc",
        "limit": PAGE_LIMIT,
    }
    while True:
        payload = get_json(path, params)
        for item in payload.get("results") or []:
            rows.append(item)
        nxt = payload.get("next_url")
        if not nxt:
            break
        parsed = urlparse(nxt)
        path = parsed.path
        params = dict(parse_qsl(parsed.query))
        params.pop("apiKey", None)
    return rows


def compute_derived(row, prev_rows):
    """Add SMA20, zscore60, and venue split percentages."""
    svr = row.get("short_volume_ratio")
    nasdaq_carteret = (row.get("nasdaq_carteret_short_volume") or 0)
    short_vol = (row.get("short_volume") or 0)

    # what fraction of the short volume routed through nasdaq carteret
    # (the largest dark/off-exchange print venue)
    carteret_pct = (nasdaq_carteret / short_vol * 100.0) if short_vol else 0.0

    # rolling stats vs previous rows (already-processed)
    svrs_recent = [r["short_volume_ratio"] for r in prev_rows[-19:]] + [svr]
    svrs_recent = [v for v in svrs_recent if v is not None]
    sma20 = sum(svrs_recent) / len(svrs_recent) if len(svrs_recent) >= 5 else None

    svrs_60 = [r["short_volume_ratio"] for r in prev_rows[-59:]] + [svr]
    svrs_60 = [v for v in svrs_60 if v is not None]
    if len(svrs_60) >= 20:
        mean = sum(svrs_60) / len(svrs_60)
        var = sum((v - mean) ** 2 for v in svrs_60) / len(svrs_60)
        stdev = var ** 0.5
        z = ((svr - mean) / stdev) if (stdev and svr is not None) else None
    else:
        z = None

    return {
        "date": row["date"],
        "ticker": row["ticker"],
        "total_volume": round(row.get("total_volume") or 0, 2),
        "short_volume": round(row.get("short_volume") or 0, 2),
        "short_volume_ratio": svr,
        "carteret_pct_of_short": round(carteret_pct, 4),
        "nyse_short_volume": row.get("nyse_short_volume") or 0,
        "nasdaq_carteret_short_volume": row.get("nasdaq_carteret_short_volume") or 0,
        "nasdaq_chicago_short_volume": row.get("nasdaq_chicago_short_volume") or 0,
        "adf_short_volume": row.get("adf_short_volume") or 0,
        "exempt_volume": row.get("exempt_volume") or 0,
        "svr_sma20": round(sma20, 4) if sma20 is not None else "",
        "svr_zscore60": round(z, 4) if z is not None else "",
    }


def write_components(ticker, rows):
    path = DATA_DIR / f"{ticker}_SVR_components.csv"
    fields = [
        "date", "ticker", "total_volume", "short_volume", "short_volume_ratio",
        "carteret_pct_of_short",
        "nyse_short_volume", "nasdaq_carteret_short_volume",
        "nasdaq_chicago_short_volume", "adf_short_volume", "exempt_volume",
        "svr_sma20", "svr_zscore60",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    print(f"Wrote {path}")


def write_seed_csv(ticker, rows):
    """Pine Seeds-style CSV (kept for parity w/ Optix). Plots SVR (0-100)."""
    path = DATA_DIR / f"{ticker}_SVR.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["time","open","high","low","close","volume"])
        w.writeheader()
        for r in rows:
            v = r.get("short_volume_ratio")
            if v is None: continue
            val = f"{float(v):.4f}"
            w.writerow({"time": r["date"], "open": val, "high": val, "low": val, "close": val, "volume": "0"})
    print(f"Wrote {path}")


def write_ts_file(ticker, rows, field, suffix):
    """TradeStation ASCII import - one line per day."""
    path = DATA_DIR / f"{ticker}_{suffix}.txt"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Date","Open","High","Low","Close","Volume"])
        w.writeheader()
        for r in rows:
            v = r.get(field)
            if v in ("", None): continue
            try: val = f"{float(v):.4f}"
            except (TypeError, ValueError): continue
            d = datetime.strptime(r["date"], "%Y-%m-%d").strftime("%m/%d/%Y")
            w.writerow({"Date": d, "Open": val, "High": val, "Low": val, "Close": val, "Volume": "0"})
    print(f"Wrote {path}")


def write_debug(ticker, rows):
    path = DATA_DIR / f"{ticker}_SVR_debug.json"
    latest = rows[-1] if rows else {}
    with path.open("w") as f:
        json.dump({
            "ticker": ticker,
            "rows_written": len(rows),
            "latest_row": latest,
            "how_to_read": {
                "short_volume_ratio": "Daily % of total volume sold short. 50% is typical.",
                "above_60": "Heavy shorting pressure - watch for capitulation or squeeze",
                "below_40": "Shorts retreating - usually bullish continuation",
                "carteret_pct_of_short": "% of shorts routed through NASDAQ Carteret (FINRA TRF). Higher = more off-exchange/institutional short flow.",
                "svr_zscore60": "z-score vs trailing 60d. |z| > 2 = unusual day.",
            },
            "tradestation_symbols": {
                "primary":   f"{ticker}_SVR_TS",
                "carteret":  f"{ticker}_SVR_NASDAQ_TS",
            },
        }, f, indent=2, default=str)
    print(f"Wrote {path}")


def process_ticker(ticker):
    today = date.today()
    start = (today - timedelta(days=BACKFILL_DAYS)).isoformat()
    end = today.isoformat()
    print(f"\n=== {ticker} : {start} -> {end} ===")

    raw = fetch_short_volume(ticker, start, end)
    if not raw:
        print(f"  no data for {ticker}")
        return

    raw.sort(key=lambda r: r["date"])

    rows = []
    for raw_row in raw:
        rows.append(compute_derived(raw_row, rows))

    write_components(ticker, rows)
    write_seed_csv(ticker, rows)
    write_ts_file(ticker, rows, "short_volume_ratio",  "SVR_TS")
    write_ts_file(ticker, rows, "carteret_pct_of_short", "SVR_NASDAQ_TS")
    write_debug(ticker, rows)


def main():
    tickers = read_tickers()
    print(f"Tickers: {tickers}")
    for t in tickers:
        try:
            process_ticker(t)
        except Exception as e:
            print(f"ERROR {t}: {e}")
    print("\nDone.")


if __name__ == "__main__":
    main()
