import csv
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

    for attempt in range(3):
        try:
            response = requests.get(url, params=params, timeout=90)
            print(f"GET {response.url.replace(API_KEY, '***')}")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.ReadTimeout:
            print(f"Timeout attempt {attempt + 1}/3")
            if attempt == 2:
                raise


def daterange(start, end):
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def stock_daily(ticker, day):
    payload = get_json(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{day}/{day}",
        {"adjusted": "true"},
    )
    results = payload.get("results") or []
    if not results:
        return None

    row = results[0]
    return {
        "close": row.get("c"),
        "volume": row.get("v"),
    }


def options_summary_for_date(ticker, day):
    path = f"/v3/snapshot/options/{ticker}"
    params = {
        "limit": 250,
        "sort": "ticker",
        "order": "asc",
        "date": str(day),
    }

    call_volume = 0.0
    put_volume = 0.0
    call_open_interest = 0.0
    put_open_interest = 0.0
    contract_count = 0
    page_count = 0

    while True:
        payload = get_json(path, params)
        results = payload.get("results") or []

        for contract in results:
            details = contract.get("details") or {}
            day_data = contract.get("day") or {}

            contract_type = details.get("contract_type")
            volume = day_data.get("volume") or 0
            open_interest = contract.get("open_interest") or 0

            try:
                volume = float(volume)
            except Exception:
                volume = 0.0

            try:
                open_interest = float(open_interest)
            except Exception:
                open_interest = 0.0

            if contract_type == "call":
                call_volume += volume
                call_open_interest += open_interest
            elif contract_type == "put":
                put_volume += volume
                put_open_interest += open_interest

            contract_count += 1

        page_count += 1
        next_url = payload.get("next_url")
        if not next_url:
            break

        if "api.massive.com" in next_url:
            path = next_url.replace("https://api.massive.com", "").split("?")[0]
            query = next_url.split("?", 1)[1] if "?" in next_url else ""
            params = {}
            for part in query.split("&"):
                if not part:
                    continue
                key, _, value = part.partition("=")
                if key != "apiKey":
                    params[key] = value
        else:
            break

        if page_count > 50:
            break

    return {
        "contract_count": contract_count,
        "call_volume": call_volume,
        "put_volume": put_volume,
        "call_open_interest": call_open_interest,
        "put_open_interest": put_open_interest,
        "put_call_volume_ratio": put_volume / call_volume if call_volume > 0 else "",
        "put_call_open_interest_ratio": put_open_interest / call_open_interest if call_open_interest > 0 else "",
    }


def write_rows(ticker, rows):
    path = DATA_DIR / f"{ticker}_components.csv"

    existing = []
    if path.exists():
        with path.open("r", newline="") as f:
            existing = list(csv.DictReader(f))

    by_date = {row["date"]: row for row in existing}
    for row in rows:
        by_date[row["date"]] = row

    fieldnames = [
        "date",
        "ticker",
        "close",
        "volume",
        "short_interest",
        "avg_daily_volume",
        "days_to_cover",
        "short_volume_ratio",
        "call_volume",
        "put_volume",
        "put_call_volume_ratio",
        "call_open_interest",
        "put_open_interest",
        "put_call_open_interest_ratio",
        "optix",
    ]

    merged = [by_date[d] for d in sorted(by_date)]

    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(merged)

    print(f"Wrote {len(merged)} rows to {path}")


def main():
    ticker = "SHOP"

    # Small test window first.
    start = date(2026, 5, 1)
    end = date(2026, 5, 5)

    rows = []

    for day in daterange(start, end):
        print(f"\nBackfilling {ticker} {day}")

        stock = stock_daily(ticker, day)
        if not stock:
            print("No stock data, skipping")
            continue

        options = options_summary_for_date(ticker, day)

        row = {
            "date": str(day),
            "ticker": ticker,
            "close": stock.get("close"),
            "volume": stock.get("volume"),
            "short_interest": "",
            "avg_daily_volume": "",
            "days_to_cover": "",
            "short_volume_ratio": "",
            "call_volume": options.get("call_volume"),
            "put_volume": options.get("put_volume"),
            "put_call_volume_ratio": options.get("put_call_volume_ratio"),
            "call_open_interest": options.get("call_open_interest"),
            "put_open_interest": options.get("put_open_interest"),
            "put_call_open_interest_ratio": options.get("put_call_open_interest_ratio"),
            "optix": "",
        }

        rows.append(row)

    write_rows(ticker, rows)


if __name__ == "__main__":
    main()
