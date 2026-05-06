import csv
import json
import os
from datetime import date, timedelta
from pathlib import Path

import requests

API_KEY = os.environ.get("MASSIVE_API_KEY")

if not API_KEY:
    raise RuntimeError("Missing MASSIVE_API_KEY")

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

BASE_URL = "https://api.massive.com"


def get_json(path, params=None):
    params = dict(params or {})
    params["apiKey"] = API_KEY
    url = f"{BASE_URL}{path}"
    response = requests.get(url, params=params, timeout=30)
    print(f"GET {response.url.replace(API_KEY, '***')}")
    response.raise_for_status()
    return response.json()


def read_tickers():
    tickers_path = ROOT / "tickers.csv"
    with tickers_path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        return [row["ticker"].strip().upper() for row in reader if row.get("ticker")]


def latest_stock_close(ticker):
    payload = get_json(f"/v2/aggs/ticker/{ticker}/prev", {"adjusted": "true"})
    results = payload.get("results") or []
    if not results:
        return None

    row = results[0]
    return {
        "close": row.get("c"),
        "volume": row.get("v"),
    }


def latest_short_interest(ticker):
    payload = get_json(
        "/stocks/v1/short-interest",
        {
            "ticker": ticker,
            "limit": 1,
            "sort": "settlement_date",
            "order": "desc",
        },
    )
    results = payload.get("results") or []
    if not results:
        return None
    return results[0]


def latest_short_volume(ticker):
    payload = get_json(
        "/stocks/v1/short-volume",
        {
            "ticker": ticker,
            "limit": 1,
            "sort": "date",
            "order": "desc",
        },
    )
    results = payload.get("results") or []
    if not results:
        return None
    return results[0]


def score_from_short_interest(short_interest):
    if not short_interest:
        return 50.0

    days_to_cover = short_interest.get("days_to_cover")
    short_percent = (
        short_interest.get("short_percent_of_float")
        or short_interest.get("short_interest_percent_float")
        or short_interest.get("percent_of_float")
    )

    score = 50.0

    if days_to_cover is not None:
        try:
            days = float(days_to_cover)
            score -= min(35.0, days * 4.0)
        except Exception:
            pass

    if short_percent is not None:
        try:
            pct = float(short_percent)
            score -= min(35.0, pct * 1.5)
        except Exception:
            pass

    return max(0.0, min(100.0, score))


def score_from_short_volume(short_volume):
    if not short_volume:
        return 50.0

    short_volume_value = (
        short_volume.get("short_volume")
        or short_volume.get("shortVolume")
        or short_volume.get("short_volume_total")
    )
    total_volume_value = (
        short_volume.get("total_volume")
        or short_volume.get("totalVolume")
        or short_volume.get("volume")
    )

    if not short_volume_value or not total_volume_value:
        return 50.0

    try:
        ratio = float(short_volume_value) / float(total_volume_value)
    except Exception:
        return 50.0

    return max(0.0, min(100.0, 100.0 - ratio * 100.0))


def write_seed_file(ticker, optix_value):
    path = DATA_DIR / f"{ticker}_OPTIX.csv"
    today = date.today().isoformat()

    rows = []
    if path.exists():
        with path.open("r", newline="") as f:
            rows = list(csv.DictReader(f))

    rows = [row for row in rows if row["time"] != today]

    rows.append(
        {
            "time": today,
            "open": f"{optix_value:.4f}",
            "high": f"{optix_value:.4f}",
            "low": f"{optix_value:.4f}",
            "close": f"{optix_value:.4f}",
            "volume": "0",
        }
    )

    rows.sort(key=lambda r: r["time"])

    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {path}: {optix_value:.4f}")


def main():
    tickers = read_tickers()
    print(f"Tickers: {tickers}")

    for ticker in tickers:
        print(f"\nProcessing {ticker}")

        stock = latest_stock_close(ticker)
        short_interest = latest_short_interest(ticker)
        short_volume = latest_short_volume(ticker)

        short_interest_score = score_from_short_interest(short_interest)
        short_volume_score = score_from_short_volume(short_volume)

        # Temporary first-pass score until options data is added.
        # 50 represents neutral for missing components.
        optix = (
            short_interest_score
            + short_volume_score
            + 50.0
            + 50.0
            + 50.0
            + 50.0
        ) / 6.0

        debug_path = DATA_DIR / f"{ticker}_debug.json"
        with debug_path.open("w") as f:
            json.dump(
                {
                    "ticker": ticker,
                    "stock": stock,
                    "short_interest": short_interest,
                    "short_volume": short_volume,
                    "short_interest_score": short_interest_score,
                    "short_volume_score": short_volume_score,
                    "optix_first_pass": optix,
                },
                f,
                indent=2,
                default=str,
            )

        write_seed_file(ticker, optix)

    print("\nDone.")


if __name__ == "__main__":
    main()
