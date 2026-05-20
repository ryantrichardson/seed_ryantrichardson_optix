"""
Export OHLCV CSVs from Massive.com for TCT / Triple Chart Trend calibration.

For a given ticker and date range, downloads OHLCV bars across the standard
TCT timeframes and writes one CSV per timeframe plus a manifest JSON.

Timeframes:
  - Daily-class: 13D, 8D, 1W (5D), 3D, 1D
  - Minute-class: 233m, 144m, 89m, 55m, 34m, 21m, 13m, 8m, 5m, 3m

Environment:
  MASSIVE_API_KEY  required, unless --dry-run is passed
  TICKER           default QQQ (or --ticker)
  FROM_DATE        YYYY-MM-DD (or --from-date)
  TO_DATE          YYYY-MM-DD (or --to-date)
  OUTPUT_DIR       default ./data/tct_export (or --output-dir)

The script never prints the API key. On HTTP errors it prints status and a
sanitized snippet of the response body, never the request URL with the key
in headers (the Authorization header is not echoed).

Massive aggregates endpoint:
  /v2/aggs/ticker/{TICKER}/range/{multiplier}/{timespan}/{from}/{to}
  ?adjusted=true&sort=asc&limit=50000
Pagination via response field `next_url` is followed if present.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

BASE_URL = "https://api.massive.com"

# (label, multiplier, timespan)
TCT_TIMEFRAMES: list[tuple[str, int, str]] = [
    ("13D", 13, "day"),
    ("8D",   8, "day"),
    ("1W",   1, "week"),
    ("3D",   3, "day"),
    ("1D",   1, "day"),
    ("233m", 233, "minute"),
    ("144m", 144, "minute"),
    ("89m",   89, "minute"),
    ("55m",   55, "minute"),
    ("34m",   34, "minute"),
    ("21m",   21, "minute"),
    ("13m",   13, "minute"),
    ("8m",     8, "minute"),
    ("5m",     5, "minute"),
    ("3m",     3, "minute"),
]


@dataclass
class TimeframeResult:
    label: str
    multiplier: int
    timespan: str
    rows: int
    first_ts: str | None
    last_ts: str | None
    csv_path: str


def _validate_date(s: str, label: str) -> str:
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError as e:
        raise SystemExit(f"error: {label} must be YYYY-MM-DD (got {s!r})") from e
    return s


def _redact(msg: str, api_key: str | None) -> str:
    if api_key and api_key in msg:
        return msg.replace(api_key, "***REDACTED***")
    return msg


def fetch_aggregates(
    session,
    api_key: str,
    ticker: str,
    multiplier: int,
    timespan: str,
    from_date: str,
    to_date: str,
    max_pages: int = 100,
    sleep_between: float = 0.0,
) -> list[dict]:
    """Fetch all OHLCV rows for the given timeframe, following next_url pagination."""
    url = (
        f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}"
        f"/{from_date}/{to_date}"
    )
    params = {"adjusted": "true", "sort": "asc", "limit": 50000}
    headers = {"Authorization": f"Bearer {api_key}"}

    results: list[dict] = []
    pages = 0
    next_url: str | None = None

    while True:
        pages += 1
        if pages > max_pages:
            print(f"  warn: stopped at max_pages={max_pages}", file=sys.stderr)
            break

        if next_url:
            resp = session.get(next_url, headers=headers, timeout=60)
        else:
            resp = session.get(url, headers=headers, params=params, timeout=60)

        if resp.status_code != 200:
            snippet = _redact((resp.text or "")[:300], api_key)
            raise RuntimeError(
                f"HTTP {resp.status_code} fetching {multiplier}/{timespan}: {snippet}"
            )

        payload = resp.json()
        batch = payload.get("results") or []
        results.extend(batch)

        next_url = payload.get("next_url")
        if not next_url:
            break
        if sleep_between:
            time.sleep(sleep_between)

    return results


def write_csv(rows: list[dict], out_path: str) -> tuple[int, str | None, str | None]:
    """Write OHLCV rows to CSV. Returns (count, first_iso, last_iso)."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    first_iso = last_iso = None
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_ms", "datetime_utc", "open", "high", "low", "close", "volume", "vwap", "transactions"])
        for r in rows:
            ts = r.get("t")
            iso = (
                datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
                if isinstance(ts, (int, float))
                else ""
            )
            if first_iso is None:
                first_iso = iso
            last_iso = iso
            w.writerow([
                ts,
                iso,
                r.get("o"),
                r.get("h"),
                r.get("l"),
                r.get("c"),
                r.get("v"),
                r.get("vw"),
                r.get("n"),
            ])
    return len(rows), first_iso, last_iso


def export_all(
    ticker: str,
    from_date: str,
    to_date: str,
    output_dir: str,
    timeframes: Iterable[tuple[str, int, str]] = TCT_TIMEFRAMES,
    dry_run: bool = False,
) -> list[TimeframeResult]:
    api_key = os.environ.get("MASSIVE_API_KEY", "")
    if not dry_run and not api_key:
        raise SystemExit("error: MASSIVE_API_KEY env var is required (or pass --dry-run)")

    if dry_run:
        print("dry-run: skipping HTTP calls; planning only.")
        plan: list[TimeframeResult] = []
        for label, mult, span in timeframes:
            csv_path = os.path.join(output_dir, f"{ticker}_{label}_{from_date}_{to_date}.csv")
            plan.append(TimeframeResult(label, mult, span, 0, None, None, csv_path))
            print(f"  would fetch {ticker} {mult}/{span} -> {csv_path}")
        return plan

    import requests
    session = requests.Session()

    results: list[TimeframeResult] = []
    for label, mult, span in timeframes:
        print(f"[{label}] fetching {ticker} {mult}/{span} {from_date}..{to_date}")
        try:
            rows = fetch_aggregates(session, api_key, ticker, mult, span, from_date, to_date)
        except Exception as e:
            print(f"  error: {_redact(str(e), api_key)}", file=sys.stderr)
            continue
        csv_path = os.path.join(output_dir, f"{ticker}_{label}_{from_date}_{to_date}.csv")
        n, first_iso, last_iso = write_csv(rows, csv_path)
        print(f"  wrote {n} rows -> {csv_path}  ({first_iso} .. {last_iso})")
        results.append(TimeframeResult(label, mult, span, n, first_iso, last_iso, csv_path))

    return results


def write_manifest(results: list[TimeframeResult], ticker: str, from_date: str, to_date: str, output_dir: str) -> str:
    manifest_path = os.path.join(output_dir, f"{ticker}_manifest_{from_date}_{to_date}.json")
    os.makedirs(output_dir, exist_ok=True)
    payload = {
        "ticker": ticker,
        "from": from_date,
        "to": to_date,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "files": [
            {
                "label": r.label,
                "multiplier": r.multiplier,
                "timespan": r.timespan,
                "rows": r.rows,
                "first_ts": r.first_ts,
                "last_ts": r.last_ts,
                "csv": os.path.basename(r.csv_path),
            }
            for r in results
        ],
    }
    with open(manifest_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"manifest -> {manifest_path}")
    return manifest_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Export OHLCV CSVs across TCT timeframes from Massive.com",
    )
    p.add_argument("--ticker", default=os.environ.get("TICKER", "QQQ"))
    p.add_argument("--from-date", default=os.environ.get("FROM_DATE"))
    p.add_argument("--to-date", default=os.environ.get("TO_DATE"))
    p.add_argument("--output-dir", default=os.environ.get("OUTPUT_DIR", "data/tct_export"))
    p.add_argument("--dry-run", action="store_true", help="Plan only; do not call Massive.")
    p.add_argument("--list-timeframes", action="store_true", help="Print TCT timeframes and exit.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.list_timeframes:
        for label, mult, span in TCT_TIMEFRAMES:
            print(f"{label}\t{mult}\t{span}")
        return 0

    if not args.from_date or not args.to_date:
        raise SystemExit("error: --from-date and --to-date (or FROM_DATE/TO_DATE env) are required")

    from_date = _validate_date(args.from_date, "--from-date")
    to_date = _validate_date(args.to_date, "--to-date")
    ticker = args.ticker.upper().strip()

    print(f"Exporting OHLCV for {ticker} from {from_date} to {to_date}")
    print(f"Output dir: {args.output_dir}")

    results = export_all(ticker, from_date, to_date, args.output_dir, dry_run=args.dry_run)
    if not args.dry_run:
        write_manifest(results, ticker, from_date, to_date, args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
