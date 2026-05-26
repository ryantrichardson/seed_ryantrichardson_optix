#!/usr/bin/env python3
"""Merge QQQ + PLTR + TSLA ghost-wick CSVs into one JSON for enrichment.

Filters to the 1.0-2.0% sweet spot to match the live alerter's behavior.
"""
import csv
import json
from pathlib import Path

DATA = Path("data")
TICKERS = ["QQQ", "PLTR", "TSLA"]
OUT = DATA / "all_wicks_for_enrichment.json"

WICK_MIN = 1.0
WICK_MAX = 2.0


def load(ticker: str):
    path = DATA / f"ghost_wicks_v2_{ticker}_trade.csv"
    rows = []
    for r in csv.DictReader(path.open()):
        try:
            wp = float(r["wick_pct"])
        except (ValueError, KeyError):
            continue
        if not (WICK_MIN <= wp <= WICK_MAX):
            continue
        r["ticker"] = ticker
        rows.append(r)
    return rows


def main():
    merged = []
    for t in TICKERS:
        rows = load(t)
        print(f"{t}: {len(rows)} wicks in {WICK_MIN}-{WICK_MAX}%")
        merged.extend(rows)
    OUT.write_text(json.dumps(merged, indent=2))
    print(f"\nTOTAL: {len(merged)} wicks -> {OUT}")


if __name__ == "__main__":
    main()
