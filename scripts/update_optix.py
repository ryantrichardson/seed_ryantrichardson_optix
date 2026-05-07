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
    response = requests.get(url, params=params, timeout=90)
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
            "sort": "settlement_date.desc",
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
            "sort": "date.desc",
        },
    )
    results = payload.get("results") or []
    if not results:
        return None
    return results[0]
    
def latest_options_summary(ticker):
    path = f"/v3/snapshot/options/{ticker}"
    params = {
        "limit": 250,
        "sort": "ticker",
        "order": "asc",
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
            day = contract.get("day") or {}

            contract_type = details.get("contract_type")
            volume = day.get("volume") or 0
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

        # Massive next_url already contains the next page query.
        # Convert it back into path/params for get_json.
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

    put_call_volume_ratio = put_volume / call_volume if call_volume > 0 else None
    put_call_open_interest_ratio = put_open_interest / call_open_interest if call_open_interest > 0 else None

    return {
        "contract_count": contract_count,
        "call_volume": call_volume,
        "put_volume": put_volume,
        "call_open_interest": call_open_interest,
        "put_open_interest": put_open_interest,
        "put_call_volume_ratio": put_call_volume_ratio,
        "put_call_open_interest_ratio": put_call_open_interest_ratio,
    }
    
def score_from_put_call_ratio(ratio):
    if ratio is None:
        return 50.0

    try:
        ratio = float(ratio)
    except Exception:
        return 50.0

    # Lower put/call = more bullish. Higher put/call = more bearish.
    # Ratio around 1.0 is neutral.
    score = 100.0 - (ratio * 50.0)
    return max(0.0, min(100.0, score))


def score_from_price_activity(stock):
    if not stock:
        return 50.0

    # Placeholder neutral until we add historical price momentum.
    return 50.0
def safe_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def percentile_rank(values, current_value):
    clean_values = [v for v in values if v is not None]

    if current_value is None or not clean_values:
        return 50.0

    count = sum(1 for v in clean_values if current_value >= v)
    return 100.0 * count / len(clean_values)


def inverse_percentile_rank(values, current_value):
    return 100.0 - percentile_rank(values, current_value)


def calculate_ranked_optix(ticker, raw_components):
    path = DATA_DIR / f"{ticker}_components.csv"

    if not path.exists():
        return raw_components["fallback_optix"]

    with path.open("r", newline="") as f:
        rows = list(csv.DictReader(f))

    if len(rows) < 20:
        return raw_components["fallback_optix"]

    days_to_cover_values = [safe_float(row.get("days_to_cover")) for row in rows]
    short_volume_ratio_values = [safe_float(row.get("short_volume_ratio")) for row in rows]
    put_call_volume_values = [safe_float(row.get("put_call_volume_ratio")) for row in rows]
    put_call_oi_values = [safe_float(row.get("put_call_open_interest_ratio")) for row in rows]

    current_days_to_cover = raw_components["days_to_cover"]
    current_short_volume_ratio = raw_components["short_volume_ratio"]
    current_put_call_volume_ratio = raw_components["put_call_volume_ratio"]
    current_put_call_oi_ratio = raw_components["put_call_open_interest_ratio"]

    analyst_score = 50.0
    price_activity_score = raw_components["price_activity_score"]

    short_interest_score = inverse_percentile_rank(days_to_cover_values, current_days_to_cover)
    short_volume_score = inverse_percentile_rank(short_volume_ratio_values, current_short_volume_ratio)
    put_call_volume_score = inverse_percentile_rank(put_call_volume_values, current_put_call_volume_ratio)
    put_call_open_interest_score = inverse_percentile_rank(put_call_oi_values, current_put_call_oi_ratio)

    optix = (
        analyst_score
        + short_interest_score
        + short_volume_score
        + put_call_open_interest_score
        + put_call_volume_score
        + price_activity_score
    ) / 6.0

    return max(0.0, min(100.0, optix))

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

def write_component_history(ticker, stock, short_interest, short_volume, options_summary, optix_value):
    path = DATA_DIR / f"{ticker}_components.csv"
    today = date.today().isoformat()

    row = {
        "date": today,
        "ticker": ticker,
        "close": stock.get("close") if stock else "",
        "volume": stock.get("volume") if stock else "",
        "short_interest": short_interest.get("short_interest") if short_interest else "",
        "avg_daily_volume": short_interest.get("avg_daily_volume") if short_interest else "",
        "days_to_cover": short_interest.get("days_to_cover") if short_interest else "",
        "short_volume_ratio": short_volume.get("short_volume_ratio") if short_volume else "",
        "call_volume": options_summary.get("call_volume"),
        "put_volume": options_summary.get("put_volume"),
        "put_call_volume_ratio": options_summary.get("put_call_volume_ratio"),
        "call_open_interest": options_summary.get("call_open_interest"),
        "put_open_interest": options_summary.get("put_open_interest"),
        "put_call_open_interest_ratio": options_summary.get("put_call_open_interest_ratio"),
        "optix": optix_value,
    }

    rows = []
    if path.exists():
        with path.open("r", newline="") as f:
            rows = list(csv.DictReader(f))

    rows = [existing for existing in rows if existing["date"] != today]
    rows.append(row)
    rows.sort(key=lambda r: r["date"])

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

    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote component history {path}")
def write_seed_file(ticker, optix_value):
    def write_tradestation_file(ticker, optix_value):
    path = DATA_DIR / f"{ticker}_TS.txt"
    today = date.today().strftime("%m/%d/%Y")

    rows = []
    if path.exists():
        with path.open("r", newline="") as f:
            rows = list(csv.DictReader(f))

    rows = [row for row in rows if row["Date"] != today]

    value = f"{optix_value:.4f}"

    rows.append(
        {
            "Date": today,
            "Open": value,
            "High": value,
            "Low": value,
            "Close": value,
            "Volume": "0",
        }
    )

    rows.sort(key=lambda r: r["Date"])

    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Date", "Open", "High", "Low", "Close", "Volume"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote TradeStation file {path}: {value}")
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
        options_summary = latest_options_summary(ticker)


        short_interest_score = score_from_short_interest(short_interest)
        short_volume_score = score_from_short_volume(short_volume)
        put_call_volume_score = score_from_put_call_ratio(options_summary.get("put_call_volume_ratio"))
        put_call_open_interest_score = score_from_put_call_ratio(options_summary.get("put_call_open_interest_ratio"))
        price_activity_score = score_from_price_activity(stock)

        # Six-part Optix-style score:
        # 1 analyst proxy: neutral for now
        # 2 short interest / average volume
        # 3 short volume / short pressure
        # 4 put/call open interest
        # 5 put/call volume
        # 6 price activity
        analyst_score = 50.0

        optix = (
            analyst_score
            + short_interest_score
            + short_volume_score
            + put_call_open_interest_score
            + put_call_volume_score
            + price_activity_score
        ) / 6.0
        raw_components = {
            "days_to_cover": safe_float(short_interest.get("days_to_cover")) if short_interest else None,
            "short_volume_ratio": safe_float(short_volume.get("short_volume_ratio")) if short_volume else None,
            "put_call_volume_ratio": safe_float(options_summary.get("put_call_volume_ratio")),
            "put_call_open_interest_ratio": safe_float(options_summary.get("put_call_open_interest_ratio")),
            "price_activity_score": price_activity_score,
            "fallback_optix": optix,
        }

        optix = calculate_ranked_optix(ticker, raw_components)
        debug_path = DATA_DIR / f"{ticker}_debug.json"
        with debug_path.open("w") as f:
            json.dump(
                {
                    "ticker": ticker,
                    "stock": stock,
                    "short_interest": short_interest,
                    "short_volume": short_volume,
                    "options_summary": options_summary,
                    "short_interest_score": short_interest_score,
                    "short_volume_score": short_volume_score,
                    "put_call_volume_score": put_call_volume_score,
                    "put_call_open_interest_score": put_call_open_interest_score,
                    "price_activity_score": price_activity_score,
                    "optix_first_pass": optix,
                },
                f,
                indent=2,
                default=str,
            )


        write_component_history(ticker, stock, short_interest, short_volume, options_summary, optix)
        write_seed_file(ticker, optix)
        write_tradestation_file(ticker, optix)

    print("\nDone.")


if __name__ == "__main__":
    main()
