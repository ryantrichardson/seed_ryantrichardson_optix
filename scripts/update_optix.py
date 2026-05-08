import csv
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

API_KEY = os.environ.get("MASSIVE_API_KEY")

if not API_KEY:
    raise RuntimeError("Missing MASSIVE_API_KEY")

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

BASE_URL = "https://api.massive.com"

BACKFILL_DAYS = 730


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


def clamp(value):
    return max(0.0, min(100.0, value))


def fetch_stock_history(ticker, start_date, end_date):
    payload = get_json(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}",
        {
            "adjusted": "true",
            "sort": "asc",
            "limit": 50000,
        },
    )

    rows = []

    for item in payload.get("results") or []:
        row_date = datetime.utcfromtimestamp(item["t"] / 1000).date().isoformat()

        rows.append(
            {
                "date": row_date,
                "ticker": ticker,
                "close": safe_float(item.get("c")),
                "volume": safe_float(item.get("v")),
            }
        )

    return rows


def fetch_short_volume_history(ticker, start_date, end_date):
    payload = get_json(
        "/stocks/v1/short-volume",
        {
            "ticker": ticker,
            "date.gte": start_date,
            "date.lte": end_date,
            "sort": "date.asc",
            "limit": 1000,
        },
    )

    rows = {}

    for item in payload.get("results") or []:
        row_date = item.get("date")
        if not row_date:
            continue

        rows[row_date] = item

    return rows


def fetch_short_interest_history(ticker, start_date, end_date):
    payload = get_json(
        "/stocks/v1/short-interest",
        {
            "ticker": ticker,
            "settlement_date.gte": start_date,
            "settlement_date.lte": end_date,
            "sort": "settlement_date.asc",
            "limit": 1000,
        },
    )

    rows = []

    for item in payload.get("results") or []:
        settlement_date = item.get("settlement_date")
        if settlement_date:
            rows.append(item)

    rows.sort(key=lambda row: row.get("settlement_date", ""))

    return rows


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
            volume = safe_float(day.get("volume")) or 0.0
            open_interest = safe_float(contract.get("open_interest")) or 0.0

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


def latest_short_interest_as_of(short_interest_rows, row_date):
    latest = None

    for item in short_interest_rows:
        settlement_date = item.get("settlement_date")
        if settlement_date and settlement_date <= row_date:
            latest = item
        elif settlement_date and settlement_date > row_date:
            break

    return latest


def get_short_volume_ratio(short_volume):
    if not short_volume:
        return None

    direct_ratio = safe_float(short_volume.get("short_volume_ratio"))
    if direct_ratio is not None:
        return direct_ratio

    short_volume_value = (
        safe_float(short_volume.get("short_volume"))
        or safe_float(short_volume.get("shortVolume"))
        or safe_float(short_volume.get("short_volume_total"))
    )
    total_volume_value = (
        safe_float(short_volume.get("total_volume"))
        or safe_float(short_volume.get("totalVolume"))
        or safe_float(short_volume.get("volume"))
    )

    if short_volume_value is None or total_volume_value in (None, 0):
        return None

    return short_volume_value / total_volume_value


def build_raw_component_rows(ticker, stock_rows, short_volume_by_date, short_interest_rows):
    rows = []

    closes = [row["close"] for row in stock_rows]

    for index, stock in enumerate(stock_rows):
        row_date = stock["date"]

        short_volume = short_volume_by_date.get(row_date)
        short_interest = latest_short_interest_as_of(short_interest_rows, row_date)

        close = stock.get("close")
        volume = stock.get("volume")

        days_to_cover = safe_float(short_interest.get("days_to_cover")) if short_interest else None
        short_interest_value = safe_float(short_interest.get("short_interest")) if short_interest else None
        avg_daily_volume = safe_float(short_interest.get("avg_daily_volume")) if short_interest else None
        short_volume_ratio = get_short_volume_ratio(short_volume)

        momentum_20d = None
        if index >= 20 and close is not None and closes[index - 20] not in (None, 0):
            momentum_20d = (close / closes[index - 20]) - 1.0

        rows.append(
            {
                "date": row_date,
                "ticker": ticker,
                "close": close,
                "volume": volume,
                "short_interest": short_interest_value,
                "avg_daily_volume": avg_daily_volume,
                "days_to_cover": days_to_cover,
                "short_volume_ratio": short_volume_ratio,
                "momentum_20d": momentum_20d,
                "call_volume": "",
                "put_volume": "",
                "put_call_volume_ratio": None,
                "call_open_interest": "",
                "put_open_interest": "",
                "put_call_open_interest_ratio": None,
            }
        )

    return rows


def score_rows(raw_rows):
    scored_rows = []

    for index, row in enumerate(raw_rows):
        history = raw_rows[: index + 1]

        days_to_cover_values = [item.get("days_to_cover") for item in history]
        short_volume_ratio_values = [item.get("short_volume_ratio") for item in history]
        momentum_values = [item.get("momentum_20d") for item in history]

        analyst_score = 50.0

        short_interest_score = inverse_percentile_rank(
            days_to_cover_values,
            row.get("days_to_cover"),
        )

        short_volume_score = inverse_percentile_rank(
            short_volume_ratio_values,
            row.get("short_volume_ratio"),
        )

        put_call_volume_score = 50.0
        put_call_open_interest_score = 50.0

        price_activity_score = percentile_rank(
            momentum_values,
            row.get("momentum_20d"),
        )

        if index < 20:
            price_activity_score = 50.0

        optix = (
            analyst_score
            + short_interest_score
            + short_volume_score
            + put_call_open_interest_score
            + put_call_volume_score
            + price_activity_score
        ) / 6.0

        scored = dict(row)
        scored["analyst_score"] = analyst_score
        scored["short_interest_score"] = short_interest_score
        scored["short_volume_score"] = short_volume_score
        scored["put_call_volume_score"] = put_call_volume_score
        scored["put_call_open_interest_score"] = put_call_open_interest_score
        scored["price_activity_score"] = price_activity_score
        scored["optix"] = clamp(optix)

        scored_rows.append(scored)

    return scored_rows


def apply_latest_options_snapshot(ticker, scored_rows):
    if not scored_rows:
        return None

    try:
        options_summary = latest_options_summary(ticker)
    except Exception as exc:
        print(f"Could not fetch options snapshot for {ticker}: {exc}")
        return None

    latest_row = scored_rows[-1]

    latest_row["call_volume"] = options_summary.get("call_volume")
    latest_row["put_volume"] = options_summary.get("put_volume")
    latest_row["put_call_volume_ratio"] = options_summary.get("put_call_volume_ratio")
    latest_row["call_open_interest"] = options_summary.get("call_open_interest")
    latest_row["put_open_interest"] = options_summary.get("put_open_interest")
    latest_row["put_call_open_interest_ratio"] = options_summary.get("put_call_open_interest_ratio")

    history = scored_rows

    put_call_volume_values = [safe_float(item.get("put_call_volume_ratio")) for item in history]
    put_call_oi_values = [safe_float(item.get("put_call_open_interest_ratio")) for item in history]

    latest_row["put_call_volume_score"] = inverse_percentile_rank(
        put_call_volume_values,
        safe_float(latest_row.get("put_call_volume_ratio")),
    )

    latest_row["put_call_open_interest_score"] = inverse_percentile_rank(
        put_call_oi_values,
        safe_float(latest_row.get("put_call_open_interest_ratio")),
    )

    latest_row["optix"] = clamp(
        (
            latest_row["analyst_score"]
            + latest_row["short_interest_score"]
            + latest_row["short_volume_score"]
            + latest_row["put_call_open_interest_score"]
            + latest_row["put_call_volume_score"]
            + latest_row["price_activity_score"]
        )
        / 6.0
    )

    return options_summary


def write_component_history(ticker, rows):
    path = DATA_DIR / f"{ticker}_components.csv"

    fieldnames = [
        "date",
        "ticker",
        "close",
        "volume",
        "short_interest",
        "avg_daily_volume",
        "days_to_cover",
        "short_volume_ratio",
        "momentum_20d",
        "call_volume",
        "put_volume",
        "put_call_volume_ratio",
        "call_open_interest",
        "put_open_interest",
        "put_call_open_interest_ratio",
        "analyst_score",
        "short_interest_score",
        "short_volume_score",
        "put_call_volume_score",
        "put_call_open_interest_score",
        "price_activity_score",
        "optix",
    ]

    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            writer.writerow(row)

    print(f"Wrote component history {path}")


def write_seed_file(ticker, rows):
    path = DATA_DIR / f"{ticker}_OPTIX.csv"

    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "volume"])
        writer.writeheader()

        for row in rows:
            value = f"{row['optix']:.4f}"

            writer.writerow(
                {
                    "time": row["date"],
                    "open": value,
                    "high": value,
                    "low": value,
                    "close": value,
                    "volume": "0",
                }
            )

    print(f"Wrote TradingView seed file {path}")


def write_tradestation_file(ticker, rows):
    path = DATA_DIR / f"{ticker}_TS.txt"

    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Date", "Open", "High", "Low", "Close", "Volume"])
        writer.writeheader()

        for row in rows:
            row_date = datetime.strptime(row["date"], "%Y-%m-%d").strftime("%m/%d/%Y")
            value = f"{row['optix']:.4f}"

            writer.writerow(
                {
                    "Date": row_date,
                    "Open": value,
                    "High": value,
                    "Low": value,
                    "Close": value,
                    "Volume": "0",
                }
            )

    print(f"Wrote TradeStation file {path}")


def write_debug_file(ticker, scored_rows, options_summary):
    path = DATA_DIR / f"{ticker}_debug.json"

    latest_row = scored_rows[-1] if scored_rows else {}

    with path.open("w") as f:
        json.dump(
            {
                "ticker": ticker,
                "rows_written": len(scored_rows),
                "latest_row": latest_row,
                "latest_options_summary": options_summary,
                "note": "Historical rows use stock price, short volume, short interest, and price activity. Options snapshot is applied to the latest row only unless prior options data has been saved separately.",
            },
            f,
            indent=2,
            default=str,
        )

    print(f"Wrote debug file {path}")


def process_ticker(ticker):
    today = date.today()
    start_date = (today - timedelta(days=BACKFILL_DAYS)).isoformat()
    end_date = today.isoformat()

    print(f"\nProcessing {ticker}: {start_date} to {end_date}")

    stock_rows = fetch_stock_history(ticker, start_date, end_date)
    short_volume_by_date = fetch_short_volume_history(ticker, start_date, end_date)
    short_interest_rows = fetch_short_interest_history(ticker, start_date, end_date)

    if not stock_rows:
        print(f"No stock rows found for {ticker}; skipping")
        return

    raw_rows = build_raw_component_rows(
        ticker,
        stock_rows,
        short_volume_by_date,
        short_interest_rows,
    )

    scored_rows = score_rows(raw_rows)

    options_summary = apply_latest_options_snapshot(ticker, scored_rows)

    write_component_history(ticker, scored_rows)
    write_seed_file(ticker, scored_rows)
    write_tradestation_file(ticker, scored_rows)
    write_debug_file(ticker, scored_rows, options_summary)


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
