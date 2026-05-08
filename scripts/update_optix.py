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

BACKFILL_DAYS = 730

# Start small enough that GitHub Actions has a chance to finish.
# If the workflow works, we can raise this later to 365, then 730.
OPTIONS_BACKFILL_DAYS = 180

# Safety cap so one ticker does not explode into thousands of option-contract calls.
# If the workflow works, we can raise this later.
MAX_OPTION_CONTRACTS_PER_TICKER = 500

REQUEST_SLEEP_SECONDS = 0.15


def get_json(path, params=None):
    params = dict(params or {})
    params["apiKey"] = API_KEY

    url = f"{BASE_URL}{path}"

    response = requests.get(url, params=params, timeout=90)
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


def clamp(value):
    return max(0.0, min(100.0, value))


def percentile_rank(values, current_value):
    clean_values = [v for v in values if v is not None]

    if current_value is None or not clean_values:
        return 50.0

    count = sum(1 for v in clean_values if current_value >= v)
    return 100.0 * count / len(clean_values)


def inverse_percentile_rank(values, current_value):
    return 100.0 - percentile_rank(values, current_value)


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
        if row_date:
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


def fetch_option_contracts_one_side(ticker, contract_type, start_date, end_date, expired):
    contracts = []
    path = "/v3/reference/options/contracts"

    params = {
        "underlying_ticker": ticker,
        "contract_type": contract_type,
        "expired": str(expired).lower(),
        "expiration_date.gte": start_date,
        "expiration_date.lte": end_date,
        "sort": "expiration_date",
        "order": "asc",
        "limit": 1000,
    }

    while True:
        payload = get_json(path, params)

        for item in payload.get("results") or []:
            option_ticker = item.get("ticker")
            expiration_date = item.get("expiration_date")
            strike_price = safe_float(item.get("strike_price"))

            if option_ticker and expiration_date:
                contracts.append(
                    {
                        "ticker": option_ticker,
                        "contract_type": contract_type,
                        "expiration_date": expiration_date,
                        "strike_price": strike_price,
                    }
                )

        next_url = payload.get("next_url")
        if not next_url:
            break

        payload = get_json_from_next_url(next_url)

        for item in payload.get("results") or []:
            option_ticker = item.get("ticker")
            expiration_date = item.get("expiration_date")
            strike_price = safe_float(item.get("strike_price"))

            if option_ticker and expiration_date:
                contracts.append(
                    {
                        "ticker": option_ticker,
                        "contract_type": contract_type,
                        "expiration_date": expiration_date,
                        "strike_price": strike_price,
                    }
                )

        next_url = payload.get("next_url")
        if not next_url:
            break

        path = urlparse(next_url).path
        params = dict(parse_qsl(urlparse(next_url).query))
        params.pop("apiKey", None)

        if len(contracts) >= MAX_OPTION_CONTRACTS_PER_TICKER:
            break

    return contracts


def select_liquidish_contracts(contracts, stock_rows, max_contracts):
    if not contracts:
        return []

    closes = [row.get("close") for row in stock_rows if row.get("close") is not None]
    if not closes:
        return contracts[:max_contracts]

    recent_price = closes[-1]

    def score_contract(contract):
        strike = contract.get("strike_price")

        if strike is None or recent_price in (None, 0):
            moneyness_score = 999999
        else:
            moneyness_score = abs(strike / recent_price - 1.0)

        expiration = contract.get("expiration_date", "9999-99-99")

        return (moneyness_score, expiration)

    contracts = sorted(contracts, key=score_contract)

    return contracts[:max_contracts]


def fetch_option_daily_aggs(option_ticker, start_date, end_date):
    payload = get_json(
        f"/v2/aggs/ticker/{option_ticker}/range/1/day/{start_date}/{end_date}",
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
                "volume": safe_float(item.get("v")) or 0.0,
                "close": safe_float(item.get("c")),
            }
        )

    return rows


def fetch_historical_options_volume(ticker, stock_rows, start_date, end_date):
    print(f"Fetching historical options volume for {ticker}: {start_date} to {end_date}")

    expiration_end = (datetime.strptime(end_date, "%Y-%m-%d").date() + timedelta(days=120)).isoformat()

    all_contracts = []

    for expired in (True, False):
        for contract_type in ("call", "put"):
            try:
                side_contracts = fetch_option_contracts_one_side(
                    ticker=ticker,
                    contract_type=contract_type,
                    start_date=start_date,
                    end_date=expiration_end,
                    expired=expired,
                )
                all_contracts.extend(side_contracts)
            except Exception as exc:
                print(f"Could not fetch {contract_type} contracts for {ticker}, expired={expired}: {exc}")

    calls = [c for c in all_contracts if c["contract_type"] == "call"]
    puts = [c for c in all_contracts if c["contract_type"] == "put"]

    max_each_side = max(1, MAX_OPTION_CONTRACTS_PER_TICKER // 2)

    selected_contracts = (
        select_liquidish_contracts(calls, stock_rows, max_each_side)
        + select_liquidish_contracts(puts, stock_rows, max_each_side)
    )

    print(f"{ticker}: selected {len(selected_contracts)} option contracts for historical volume")

    by_date = {}

    for index, contract in enumerate(selected_contracts, start=1):
        option_ticker = contract["ticker"]
        contract_type = contract["contract_type"]

        print(f"{ticker}: option contract {index}/{len(selected_contracts)} {option_ticker}")

        try:
            agg_rows = fetch_option_daily_aggs(option_ticker, start_date, end_date)
        except Exception as exc:
            print(f"Could not fetch aggs for {option_ticker}: {exc}")
            continue

        for agg in agg_rows:
            row_date = agg["date"]

            if row_date not in by_date:
                by_date[row_date] = {
                    "call_volume": 0.0,
                    "put_volume": 0.0,
                }

            if contract_type == "call":
                by_date[row_date]["call_volume"] += agg["volume"]
            elif contract_type == "put":
                by_date[row_date]["put_volume"] += agg["volume"]

    for row_date, summary in by_date.items():
        call_volume = summary["call_volume"]
        put_volume = summary["put_volume"]

        summary["put_call_volume_ratio"] = put_volume / call_volume if call_volume > 0 else None

    return by_date


def build_raw_component_rows(ticker, stock_rows, short_volume_by_date, short_interest_rows, options_by_date):
    rows = []
    closes = [row["close"] for row in stock_rows]

    for index, stock in enumerate(stock_rows):
        row_date = stock["date"]

        short_volume = short_volume_by_date.get(row_date)
        short_interest = latest_short_interest_as_of(short_interest_rows, row_date)
        options_summary = options_by_date.get(row_date, {})

        close = stock.get("close")
        volume = stock.get("volume")

        days_to_cover = safe_float(short_interest.get("days_to_cover")) if short_interest else None
        short_interest_value = safe_float(short_interest.get("short_interest")) if short_interest else None
        avg_daily_volume = safe_float(short_interest.get("avg_daily_volume")) if short_interest else None
        short_volume_ratio = get_short_volume_ratio(short_volume)

        call_volume = safe_float(options_summary.get("call_volume"))
        put_volume = safe_float(options_summary.get("put_volume"))
        put_call_volume_ratio = safe_float(options_summary.get("put_call_volume_ratio"))

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
                "call_volume": call_volume,
                "put_volume": put_volume,
                "put_call_volume_ratio": put_call_volume_ratio,
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
        put_call_volume_values = [item.get("put_call_volume_ratio") for item in history]

        analyst_score = 50.0

        short_interest_score = inverse_percentile_rank(
            days_to_cover_values,
            row.get("days_to_cover"),
        )

        short_volume_score = inverse_percentile_rank(
            short_volume_ratio_values,
            row.get("short_volume_ratio"),
        )

        put_call_volume_score = inverse_percentile_rank(
            put_call_volume_values,
            row.get("put_call_volume_ratio"),
        )

        # We do not have historical daily open-interest history yet.
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


def write_debug_file(ticker, scored_rows, options_by_date):
    path = DATA_DIR / f"{ticker}_debug.json"

    latest_row = scored_rows[-1] if scored_rows else {}

    with path.open("w") as f:
        json.dump(
            {
                "ticker": ticker,
                "rows_written": len(scored_rows),
                "historical_options_days_found": len(options_by_date),
                "latest_row": latest_row,
                "note": "Historical options volume is estimated from selected option contracts. Open-interest history is still neutral unless added later.",
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

    options_start_date = (today - timedelta(days=OPTIONS_BACKFILL_DAYS)).isoformat()

    print(f"\nProcessing {ticker}")
    print(f"Stock/short backfill: {start_date} to {end_date}")
    print(f"Options backfill: {options_start_date} to {end_date}")

    stock_rows = fetch_stock_history(ticker, start_date, end_date)

    if not stock_rows:
        print(f"No stock rows found for {ticker}; skipping")
        return

    short_volume_by_date = fetch_short_volume_history(ticker, start_date, end_date)
    short_interest_rows = fetch_short_interest_history(ticker, start_date, end_date)

    try:
        options_by_date = fetch_historical_options_volume(
            ticker=ticker,
            stock_rows=stock_rows,
            start_date=options_start_date,
            end_date=end_date,
        )
    except Exception as exc:
        print(f"Historical options backfill failed for {ticker}: {exc}")
        options_by_date = {}

    raw_rows = build_raw_component_rows(
        ticker=ticker,
        stock_rows=stock_rows,
        short_volume_by_date=short_volume_by_date,
        short_interest_rows=short_interest_rows,
        options_by_date=options_by_date,
    )

    scored_rows = score_rows(raw_rows)

    write_component_history(ticker, scored_rows)
    write_seed_file(ticker, scored_rows)
    write_tradestation_file(ticker, scored_rows)
    write_debug_file(ticker, scored_rows, options_by_date)


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
