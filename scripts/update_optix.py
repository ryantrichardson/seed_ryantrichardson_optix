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
OPTIONS_BACKFILL_DAYS = 180
MAX_OPTION_CONTRACTS_PER_TICKER = 500
REQUEST_SLEEP_SECONDS = 0.15


def get_json(path, params=None):
    params = dict(params or {})
    params["apiKey"] = API_KEY
    response = requests.get(f"{BASE_URL}{path}", params=params, timeout=90)
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
    clean = [v for v in values if v is not None]
    if current_value is None or not clean:
        return 50.0
    count = sum(1 for v in clean if current_value >= v)
    return 100.0 * count / len(clean)


def inverse_percentile_rank(values, current_value):
    return 100.0 - percentile_rank(values, current_value)


def rolling_values(rows, key, index, length):
    start = max(0, index - length + 1)
    return [rows[i].get(key) for i in range(start, index + 1)]


def fetch_stock_history(ticker, start_date, end_date):
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
                "ticker": ticker,
                "open": safe_float(item.get("o")),
                "high": safe_float(item.get("h")),
                "low": safe_float(item.get("l")),
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
        if item.get("settlement_date"):
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

            if len(contracts) >= MAX_OPTION_CONTRACTS_PER_TICKER:
                return contracts

        next_url = payload.get("next_url")
        if not next_url:
            break

        parsed = urlparse(next_url)
        path = parsed.path
        params = dict(parse_qsl(parsed.query))
        params.pop("apiKey", None)

    return contracts


def select_liquidish_contracts(contracts, stock_rows, max_contracts):
    closes = [row.get("close") for row in stock_rows if row.get("close") is not None]
    if not closes:
        return contracts[:max_contracts]

    recent_price = closes[-1]

    def contract_score(contract):
        strike = contract.get("strike_price")
        expiration = contract.get("expiration_date", "9999-99-99")

        if strike is None or recent_price in (None, 0):
            moneyness = 999999
        else:
            moneyness = abs(strike / recent_price - 1.0)

        return (moneyness, expiration)

    return sorted(contracts, key=contract_score)[:max_contracts]


def fetch_option_daily_aggs(option_ticker, start_date, end_date):
    payload = get_json(
        f"/v2/aggs/ticker/{option_ticker}/range/1/day/{start_date}/{end_date}",
        {"adjusted": "true", "sort": "asc", "limit": 50000},
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

    expiration_end = (
        datetime.strptime(end_date, "%Y-%m-%d").date() + timedelta(days=120)
    ).isoformat()

    all_contracts = []

    for expired in (True, False):
        for contract_type in ("call", "put"):
            try:
                all_contracts.extend(
                    fetch_option_contracts_one_side(
                        ticker=ticker,
                        contract_type=contract_type,
                        start_date=start_date,
                        end_date=expiration_end,
                        expired=expired,
                    )
                )
            except Exception as exc:
                print(f"Could not fetch {contract_type} contracts for {ticker}: {exc}")

    calls = [c for c in all_contracts if c["contract_type"] == "call"]
    puts = [c for c in all_contracts if c["contract_type"] == "put"]

    max_each_side = max(1, MAX_OPTION_CONTRACTS_PER_TICKER // 2)

    selected_contracts = (
        select_liquidish_contracts(calls, stock_rows, max_each_side)
        + select_liquidish_contracts(puts, stock_rows, max_each_side)
    )

    print(f"{ticker}: selected {len(selected_contracts)} option contracts")

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
                by_date[row_date] = {"call_volume": 0.0, "put_volume": 0.0}

            if contract_type == "call":
                by_date[row_date]["call_volume"] += agg["volume"]
            elif contract_type == "put":
                by_date[row_date]["put_volume"] += agg["volume"]

    for row_date, summary in by_date.items():
        call_volume = summary["call_volume"]
        put_volume = summary["put_volume"]
        summary["put_call_volume_ratio"] = put_volume / call_volume if call_volume > 0 else None
        summary["total_options_volume"] = call_volume + put_volume

    return by_date


def simple_ma(values, index, length):
    if index + 1 < length:
        return None

    window = values[index - length + 1 : index + 1]
    clean = [v for v in window if v is not None]

    if len(clean) < length:
        return None

    return sum(clean) / len(clean)


def calc_rsi(closes, index, length=14):
    if index < length:
        return None

    gains = 0.0
    losses = 0.0

    for i in range(index - length + 1, index + 1):
        change = closes[i] - closes[i - 1]

        if change > 0:
            gains += change
        else:
            losses += abs(change)

    if gains == 0 and losses == 0:
        return 50.0

    if losses == 0:
        return 100.0

    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))


def calc_stoch(closes, index, length=20):
    if index + 1 < length:
        return None

    window = closes[index - length + 1 : index + 1]
    clean = [v for v in window if v is not None]

    if len(clean) < length:
        return None

    highest = max(clean)
    lowest = min(clean)

    if highest == lowest:
        return 50.0

    return 100.0 * (closes[index] - lowest) / (highest - lowest)


def add_price_indicators(rows):
    closes = [row.get("close") for row in rows]
    volumes = [row.get("volume") for row in rows]

    for index, row in enumerate(rows):
        close = row.get("close")

        ma20 = simple_ma(closes, index, 20)
        ma50 = simple_ma(closes, index, 50)
        ma200 = simple_ma(closes, index, 200)
        vol20 = simple_ma(volumes, index, 20)

        row["rsi14"] = calc_rsi(closes, index, 14)
        row["stoch20"] = calc_stoch(closes, index, 20)

        row["dist_ma20"] = (close / ma20 - 1.0) if close is not None and ma20 not in (None, 0) else None
        row["dist_ma50"] = (close / ma50 - 1.0) if close is not None and ma50 not in (None, 0) else None
        row["dist_ma200"] = (close / ma200 - 1.0) if close is not None and ma200 not in (None, 0) else None

        row["volume_surge"] = (
            row.get("volume") / vol20 if row.get("volume") is not None and vol20 not in (None, 0) else None
        )

        if index >= 20 and close is not None and closes[index - 20] not in (None, 0):
            row["roc20"] = close / closes[index - 20] - 1.0
        else:
            row["roc20"] = None

        if index >= 50 and ma50 is not None:
            ma50_past = simple_ma(closes, index - 20, 50)
            row["ma50_slope"] = ma50 / ma50_past - 1.0 if ma50_past not in (None, 0) else None
        else:
            row["ma50_slope"] = None

    return rows


def build_raw_component_rows(ticker, stock_rows, short_volume_by_date, short_interest_rows, options_by_date):
    rows = []

    for stock in stock_rows:
        row_date = stock["date"]

        short_volume = short_volume_by_date.get(row_date)
        short_interest = latest_short_interest_as_of(short_interest_rows, row_date)
        options_summary = options_by_date.get(row_date, {})

        days_to_cover = safe_float(short_interest.get("days_to_cover")) if short_interest else None
        short_interest_value = safe_float(short_interest.get("short_interest")) if short_interest else None
        avg_daily_volume = safe_float(short_interest.get("avg_daily_volume")) if short_interest else None
        short_volume_ratio = get_short_volume_ratio(short_volume)

        row = {
            "date": row_date,
            "ticker": ticker,
            "close": stock.get("close"),
            "volume": stock.get("volume"),
            "short_interest": short_interest_value,
            "avg_daily_volume": avg_daily_volume,
            "days_to_cover": days_to_cover,
            "short_volume_ratio": short_volume_ratio,
            "call_volume": safe_float(options_summary.get("call_volume")),
            "put_volume": safe_float(options_summary.get("put_volume")),
            "put_call_volume_ratio": safe_float(options_summary.get("put_call_volume_ratio")),
            "total_options_volume": safe_float(options_summary.get("total_options_volume")),
        }

        rows.append(row)

    return add_price_indicators(rows)


def score_entry_oscillator(rows):
    scored_rows = []

    for index, row in enumerate(rows):
        history_20 = rows[max(0, index - 19) : index + 1]
        history_60 = rows[max(0, index - 59) : index + 1]
        history_126 = rows[max(0, index - 125) : index + 1]

        rsi_score = row.get("rsi14") if row.get("rsi14") is not None else 50.0
        stoch_score = row.get("stoch20") if row.get("stoch20") is not None else 50.0

        dist20_score = percentile_rank([r.get("dist_ma20") for r in history_126], row.get("dist_ma20"))
        dist50_score = percentile_rank([r.get("dist_ma50") for r in history_126], row.get("dist_ma50"))
        roc20_score = percentile_rank([r.get("roc20") for r in history_126], row.get("roc20"))

        price_exhaustion_score = (
            rsi_score * 0.25
            + stoch_score * 0.25
            + dist20_score * 0.20
            + dist50_score * 0.15
            + roc20_score * 0.15
        )

        put_call_score_60 = inverse_percentile_rank(
            [r.get("put_call_volume_ratio") for r in history_60],
            row.get("put_call_volume_ratio"),
        )

        put_call_score_20 = inverse_percentile_rank(
            [r.get("put_call_volume_ratio") for r in history_20],
            row.get("put_call_volume_ratio"),
        )

        options_volume_surge_score = percentile_rank(
            [r.get("total_options_volume") for r in history_60],
            row.get("total_options_volume"),
        )

        # High put/call = fear = lower oscillator.
        # Low put/call = greed = higher oscillator.
        # Big options volume makes the signal more extreme.
        options_fear_greed_score = (
            put_call_score_60 * 0.60
            + put_call_score_20 * 0.30
            + (100.0 - options_volume_surge_score if put_call_score_20 < 50 else options_volume_surge_score) * 0.10
        )

        short_volume_score = inverse_percentile_rank(
            [r.get("short_volume_ratio") for r in history_60],
            row.get("short_volume_ratio"),
        )

        days_to_cover_score = inverse_percentile_rank(
            [r.get("days_to_cover") for r in history_126],
            row.get("days_to_cover"),
        )

        short_pressure_score = short_volume_score * 0.65 + days_to_cover_score * 0.35

        trend_score = 50.0

        dist_ma200 = row.get("dist_ma200")
        ma50_slope = row.get("ma50_slope")

        if dist_ma200 is not None:
            if dist_ma200 > 0:
                trend_score += 7.5
            else:
                trend_score -= 7.5

        if ma50_slope is not None:
            if ma50_slope > 0:
                trend_score += 7.5
            else:
                trend_score -= 7.5

        trend_score = clamp(trend_score)

        entry_oscillator = (
            price_exhaustion_score * 0.45
            + options_fear_greed_score * 0.35
            + short_pressure_score * 0.15
            + trend_score * 0.05
        )

        scored = dict(row)
        scored["price_exhaustion_score"] = clamp(price_exhaustion_score)
        scored["options_fear_greed_score"] = clamp(options_fear_greed_score)
        scored["short_pressure_score"] = clamp(short_pressure_score)
        scored["trend_score"] = clamp(trend_score)
        scored["optix"] = clamp(entry_oscillator)

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
        "call_volume",
        "put_volume",
        "put_call_volume_ratio",
        "total_options_volume",
        "rsi14",
        "stoch20",
        "dist_ma20",
        "dist_ma50",
        "dist_ma200",
        "roc20",
        "volume_surge",
        "ma50_slope",
        "price_exhaustion_score",
        "options_fear_greed_score",
        "short_pressure_score",
        "trend_score",
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
                "interpretation": {
                    "0_to_20": "oversold / possible buy zone",
                    "20_to_35": "watch for buy setup",
                    "35_to_65": "neutral",
                    "65_to_80": "caution / trim zone",
                    "80_to_100": "overbought / possible sell zone",
                },
                "note": "This is now a contrarian entry oscillator, not a SentimenTrader clone.",
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

    scored_rows = score_entry_oscillator(raw_rows)

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
