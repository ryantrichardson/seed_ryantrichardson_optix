"""
Update Dark Pool Ratio (DPR) data for the watchlist.

Pulls trade-level data from the Massive API, classifies each trade as
"lit" (a public exchange) or "dark" (exchange:4 + trf_id, i.e. dark pool
/ ATS / off-exchange print), and writes:

  data/{TICKER}_DPR.csv         - Pine Seeds-style CSV (kept for parity)
  data/{TICKER}_DPR_TS.txt      - TradeStation ASCII import file (the one we use)
  data/{TICKER}_DPR_components.csv  - full daily breakdown (lit/dark vol, ratio, blocks)
  data/{TICKER}_DPR_debug.json  - latest values + interpretation

Indicator value plotted in TradeStation: DPR_PCT = dark_volume / total_volume * 100
(0-100 scale, same shape as Optix so you can stack them on a chart).

Companion sub-indicator: dark block count (single trades >= 10k shares marked dark).
This is written to {TICKER}_DPR_BLOCKS_TS.txt as a separate symbol you can
add as a histogram.

Backfill: 180 calendar days, daily incremental after that.
"""

import csv
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qsl

import requests

API_KEY = os.environ.get("MASSIVE_API_KEY")

if not API_KEY:
    raise RuntimeError("Missing MASSIVE_API_KEY")

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

BASE_URL = "https://api.massive.com"

# How far back to backfill on the very first run / when components file is missing.
BACKFILL_DAYS = 180

# Minimum size (shares) for a single trade to count as a "dark block".
DARK_BLOCK_SHARES = 10_000

# Page size for the trades endpoint.
TRADES_PAGE_LIMIT = 50_000

# Polite delay between API calls.
REQUEST_SLEEP_SECONDS = 0.15

# Hard cap on how many trade pages we'll pull per ticker-day, just in case.
# At 50k trades/page, 200 pages = 10M trades for a single day.
MAX_PAGES_PER_DAY = 200


def get_json(path, params=None):
    params = dict(params or {})
    params["apiKey"] = API_KEY
    response = requests.get(f"{BASE_URL}{path}", params=params, timeout=120)
    print(f"GET {response.url.replace(API_KEY, '***')}")
    if REQUEST_SLEEP_SECONDS:
        time.sleep(REQUEST_SLEEP_SECONDS)
    response.raise_for_status()
    return response.json()


def get_json_from_next_url(next_url):
    parsed = urlparse(next_url)
    path = parsed.path
    params = dict(parse_qsl(parsed.query))
    params.pop("apiKey", None)
    return get_json(path, params)


def read_tickers():
    with (ROOT / "tickers.csv").open("r", newline="") as f:
        reader = csv.DictReader(f)
        return [row["ticker"].strip().upper() for row in reader if row.get("ticker")]


def fetch_stock_history(ticker, start_date, end_date):
    """Used for the date axis + sanity-check total volume."""
    payload = get_json(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}",
        {"adjusted": "true", "sort": "asc", "limit": 50000},
    )

    rows = []
    for item in payload.get("results") or []:
        row_date = datetime.utcfromtimestamp(item["t"] / 1000).date().isoformat()
        rows.append(
            {
                "date": row_date,
                "close": item.get("c"),
                "volume": item.get("v") or 0.0,
            }
        )
    return rows


def aggregate_dark_volume_for_day(ticker, day_iso):
    """
    Pull every trade for `ticker` on `day_iso` (YYYY-MM-DD) and classify
    lit vs dark.

    A trade is considered "dark" when:
      - exchange == 4   (FINRA TRF reporting venue)
      - AND a trf_id field is present
    This matches Massive's documented dark-pool flag.

    Returns: dict with lit_volume, dark_volume, total_volume, dark_blocks.
    """
    path = f"/v3/trades/{ticker}"
    params = {
        "timestamp": day_iso,
        "limit": TRADES_PAGE_LIMIT,
        "order": "asc",
        "sort": "timestamp",
    }

    lit_volume = 0
    dark_volume = 0
    dark_blocks = 0
    pages = 0

    while True:
        payload = get_json(path, params)
        results = payload.get("results") or []

        for trade in results:
            size = trade.get("size") or 0
            exchange = trade.get("exchange")
            trf_id = trade.get("trf_id")

            if exchange == 4 and trf_id is not None:
                dark_volume += size
                if size >= DARK_BLOCK_SHARES:
                    dark_blocks += 1
            else:
                lit_volume += size

        pages += 1
        next_url = payload.get("next_url")
        if not next_url or pages >= MAX_PAGES_PER_DAY:
            break

        parsed = urlparse(next_url)
        path = parsed.path
        params = dict(parse_qsl(parsed.query))
        params.pop("apiKey", None)

    total_volume = lit_volume + dark_volume

    return {
        "lit_volume": lit_volume,
        "dark_volume": dark_volume,
        "total_volume": total_volume,
        "dark_blocks": dark_blocks,
    }


def load_existing_components(ticker):
    """Return {date: row} of any already-computed daily DPR rows."""
    path = DATA_DIR / f"{ticker}_DPR_components.csv"
    if not path.exists():
        return {}

    existing = {}
    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            existing[row["date"]] = row
    return existing


def build_daily_rows(ticker, stock_rows, existing):
    """For each trading day in stock_rows, ensure we have a DPR row."""
    out = []

    for stock in stock_rows:
        day = stock["date"]
        if day in existing:
            out.append(existing[day])
            continue

        try:
            agg = aggregate_dark_volume_for_day(ticker, day)
        except Exception as exc:
            print(f"  could not aggregate trades for {ticker} {day}: {exc}")
            out.append(
                {
                    "date": day,
                    "ticker": ticker,
                    "close": stock.get("close"),
                    "lit_volume": "",
                    "dark_volume": "",
                    "total_volume": "",
                    "dark_pool_ratio": "",
                    "dark_pool_ratio_pct": "",
                    "dark_blocks": "",
                }
            )
            continue

        total = agg["total_volume"]
        ratio = (agg["dark_volume"] / total) if total else 0.0

        out.append(
            {
                "date": day,
                "ticker": ticker,
                "close": stock.get("close"),
                "lit_volume": agg["lit_volume"],
                "dark_volume": agg["dark_volume"],
                "total_volume": total,
                "dark_pool_ratio": round(ratio, 6),
                "dark_pool_ratio_pct": round(ratio * 100.0, 4),
                "dark_blocks": agg["dark_blocks"],
            }
        )

        print(
            f"  {day}: lit={agg['lit_volume']:,} dark={agg['dark_volume']:,} "
            f"DPR={ratio*100:.2f}% blocks={agg['dark_blocks']}"
        )

    return out


def add_rolling_stats(rows):
    """Add 20d SMA of DPR + z-score against trailing 60d."""
    dpr_values = []
    for row in rows:
        try:
            dpr_values.append(float(row.get("dark_pool_ratio_pct")) if row.get("dark_pool_ratio_pct") not in ("", None) else None)
        except (TypeError, ValueError):
            dpr_values.append(None)

    for i, row in enumerate(rows):
        # 20-day SMA
        window20 = [v for v in dpr_values[max(0, i - 19): i + 1] if v is not None]
        sma20 = sum(window20) / len(window20) if len(window20) >= 5 else None

        # 60-day z-score
        window60 = [v for v in dpr_values[max(0, i - 59): i + 1] if v is not None]
        if len(window60) >= 20:
            mean = sum(window60) / len(window60)
            variance = sum((v - mean) ** 2 for v in window60) / len(window60)
            stdev = variance ** 0.5
            current = dpr_values[i]
            z = ((current - mean) / stdev) if (stdev and current is not None) else None
        else:
            z = None

        row["dpr_sma20"] = round(sma20, 4) if sma20 is not None else ""
        row["dpr_zscore60"] = round(z, 4) if z is not None else ""

    return rows


def write_components(ticker, rows):
    path = DATA_DIR / f"{ticker}_DPR_components.csv"
    fieldnames = [
        "date", "ticker", "close",
        "lit_volume", "dark_volume", "total_volume",
        "dark_pool_ratio", "dark_pool_ratio_pct",
        "dpr_sma20", "dpr_zscore60",
        "dark_blocks",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    print(f"Wrote {path}")


def write_seed_file(ticker, rows):
    """Pine Seeds-style CSV. Plots DPR_PCT (0-100)."""
    path = DATA_DIR / f"{ticker}_DPR.csv"
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        for row in rows:
            v = row.get("dark_pool_ratio_pct")
            if v in ("", None):
                continue
            value = f"{float(v):.4f}"
            writer.writerow({
                "time": row["date"],
                "open": value, "high": value, "low": value, "close": value,
                "volume": "0",
            })
    print(f"Wrote {path}")


def write_tradestation_file(ticker, rows, field, suffix):
    """TradeStation ASCII import file. field = which row key to plot."""
    path = DATA_DIR / f"{ticker}_{suffix}.txt"
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Date", "Open", "High", "Low", "Close", "Volume"])
        writer.writeheader()
        for row in rows:
            v = row.get(field)
            if v in ("", None):
                continue
            try:
                value = f"{float(v):.4f}"
            except (TypeError, ValueError):
                continue
            row_date = datetime.strptime(row["date"], "%Y-%m-%d").strftime("%m/%d/%Y")
            writer.writerow({
                "Date": row_date,
                "Open": value, "High": value, "Low": value, "Close": value,
                "Volume": "0",
            })
    print(f"Wrote {path}")


def write_debug_file(ticker, rows):
    path = DATA_DIR / f"{ticker}_DPR_debug.json"
    latest = rows[-1] if rows else {}
    with path.open("w") as f:
        json.dump(
            {
                "ticker": ticker,
                "rows_written": len(rows),
                "latest_row": latest,
                "interpretation": {
                    "what_it_is": "Dark Pool Ratio = dark_volume / total_volume * 100",
                    "0_to_30":   "low dark activity - retail/lit dominant",
                    "30_to_45":  "normal range for most stocks",
                    "45_to_55":  "elevated institutional presence",
                    "55_plus":   "heavy dark activity - watch for block prints / accumulation",
                    "dpr_zscore60": "z-score vs trailing 60d. |z| > 2 is unusual.",
                    "dark_blocks": "count of single trades >= 10k shares routed dark",
                },
                "tradestation_symbols": {
                    "primary":   f"{ticker}_DPR_TS",
                    "blocks":    f"{ticker}_DPR_BLOCKS_TS",
                },
            },
            f, indent=2, default=str,
        )
    print(f"Wrote {path}")


def process_ticker(ticker):
    today = date.today()
    start_date = (today - timedelta(days=BACKFILL_DAYS)).isoformat()
    end_date = today.isoformat()

    print(f"\n=== {ticker} : {start_date} -> {end_date} ===")

    stock_rows = fetch_stock_history(ticker, start_date, end_date)
    if not stock_rows:
        print(f"No stock rows for {ticker}; skipping")
        return

    existing = load_existing_components(ticker)
    print(f"  existing rows on disk: {len(existing)}")

    rows = build_daily_rows(ticker, stock_rows, existing)
    rows = add_rolling_stats(rows)

    write_components(ticker, rows)
    write_seed_file(ticker, rows)
    write_tradestation_file(ticker, rows, "dark_pool_ratio_pct", "DPR_TS")
    write_tradestation_file(ticker, rows, "dark_blocks",         "DPR_BLOCKS_TS")
    write_debug_file(ticker, rows)


def main():
    tickers = read_tickers()
    print(f"Tickers: {tickers}")

    for ticker in tickers:
        try:
            process_ticker(ticker)
        except Exception as exc:
            print(f"ERROR processing {ticker}: {exc}")

    print("\nDone.")


if __name__ == "__main__":
    main()
