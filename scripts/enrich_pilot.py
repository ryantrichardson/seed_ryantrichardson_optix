#!/usr/bin/env python3
"""
Enrich the 9-wick block-classifier pilot with three new dimensions:

  1. Short Volume (FINRA ATS dark-pool short-sale volume) on the wick day
     and the 20-trading-day average prior. Asks: was the dark pool selling
     short more than usual on the day the wick fired?

  2. News + sentiment in a +/- 30 min window around the wick. Asks: is there
     a catalyst in the news feed we should be aware of?

  3. Float-normalized block size. Asks: was the wick-minute volume meaningful
     relative to the ticker's free float?

Reads:  data/pilot_block_classifier.json
Writes: data/pilot_enriched.csv  + data/pilot_enriched.json
"""
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests

API_KEY = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
ET = timezone(timedelta(hours=-4))

PILOT_FILE = Path("data/pilot_block_classifier.json")
OUT_CSV = Path("data/pilot_enriched.csv")
OUT_JSON = Path("data/pilot_enriched.json")


def get(path, params=None):
    """GET helper with retry and rate-limit handling."""
    params = dict(params or {})
    params["apiKey"] = API_KEY
    url = f"{BASE}{path}?{urlencode(params)}"
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                print(f"  [warn] {path} failed: {e}", file=sys.stderr)
                return None
            time.sleep(1 + attempt)
    return None


def fetch_short_volume(ticker: str, wick_date: str):
    """Get short volume for wick day and ~30 trading days prior. Returns
    (wick_day_record, baseline_dict). Baseline = avg of prior 20 sessions."""
    # Pull a 45-day window ending at wick_date
    end = datetime.strptime(wick_date, "%Y-%m-%d").date()
    start = end - timedelta(days=45)
    data = get(
        "/stocks/v1/short-interest",  # placeholder fallback if short-volume url differs
        None,
    )
    # Real call
    data = get(
        "/stocks/v1/short-volume",
        {
            "ticker": ticker,
            "date.gte": start.isoformat(),
            "date.lte": end.isoformat(),
            "order": "desc",
            "limit": 50,
        },
    )
    if not data or "results" not in data:
        return None, None
    rows = data["results"]
    if not rows:
        return None, None
    # First row should be the wick day (or closest prior session)
    wick_row = None
    prior = []
    for row in rows:
        if row.get("date") == wick_date:
            wick_row = row
        elif row.get("date", "") < wick_date:
            prior.append(row)
    if wick_row is None and rows:
        # No exact match; use closest prior session as the "wick day proxy"
        wick_row = rows[0]
        prior = rows[1:]
    prior = prior[:20]
    if not prior:
        return wick_row, None
    baseline = {
        "avg_short_volume": sum(r.get("short_volume", 0) or 0 for r in prior) / len(prior),
        "avg_short_ratio": sum(r.get("short_volume_ratio", 0) or 0 for r in prior) / len(prior),
        "n_sessions": len(prior),
    }
    return wick_row, baseline


def fetch_news(ticker: str, wick_datetime_iso: str):
    """News articles within +/- 30 min of the wick. Returns list of
    {published_utc, title, sentiment_label, sentiment_reasoning}."""
    wick_dt = datetime.fromisoformat(wick_datetime_iso)
    win_start = (wick_dt - timedelta(minutes=30)).astimezone(timezone.utc)
    win_end = (wick_dt + timedelta(minutes=30)).astimezone(timezone.utc)
    data = get(
        "/v2/reference/news",
        {
            "ticker": ticker,
            "published_utc.gte": win_start.isoformat().replace("+00:00", "Z"),
            "published_utc.lte": win_end.isoformat().replace("+00:00", "Z"),
            "limit": 20,
            "order": "asc",
        },
    )
    if not data or "results" not in data:
        return []
    out = []
    for art in data["results"]:
        insight = None
        for ins in (art.get("insights") or []):
            if ins.get("ticker", "").upper() == ticker.upper():
                insight = ins
                break
        out.append({
            "published_utc": art.get("published_utc"),
            "title": art.get("title", ""),
            "publisher": (art.get("publisher") or {}).get("name", ""),
            "sentiment": (insight or {}).get("sentiment"),
            "reasoning": ((insight or {}).get("sentiment_reasoning") or "")[:200],
        })
    return out


def fetch_float(ticker: str):
    """Most recent free float in shares."""
    data = get("/stocks/vX/float", {"ticker": ticker, "limit": 1, "order": "desc"})
    if not data or "results" not in data or not data["results"]:
        return None
    row = data["results"][0]
    # Field name per Massive docs is typically "value" or "float"; capture all candidates
    for key in ("free_float", "float", "value", "shares"):
        if key in row and row[key]:
            return {"float_shares": row[key], "as_of": row.get("date") or row.get("as_of")}
    # Fall back to whole row if structure is unexpected
    return {"raw": row}


def enrich_wick(w: dict) -> dict:
    ticker = w["ticker"]
    date = w["date"]
    dt_iso = w["datetime"]
    wick_vol = int(float(w.get("volume", 0)))
    print(f"\n=== {ticker} {date} {w['time_et']} ET  ({w['direction']} {w['wick_pct']}%) ===")

    # 1) Short volume
    sv_day, sv_base = fetch_short_volume(ticker, date)
    sv_summary = None
    if sv_day:
        sv = int(sv_day.get("short_volume") or 0)
        ratio = float(sv_day.get("short_volume_ratio") or 0)
        sv_summary = {
            "date": sv_day.get("date"),
            "short_volume": sv,
            "total_volume": sv_day.get("total_volume"),
            "short_volume_ratio_pct": round(ratio, 2),
        }
        if sv_base:
            sv_summary["20d_avg_short_volume"] = round(sv_base["avg_short_volume"])
            sv_summary["20d_avg_short_ratio_pct"] = round(sv_base["avg_short_ratio"], 2)
            if sv_base["avg_short_volume"]:
                sv_summary["short_vol_zscore_simple"] = round(
                    sv / sv_base["avg_short_volume"], 2
                )
        if sv_base:
            print(f"  short_vol day={sv:,d} ratio={ratio:.2f}%  "
                  f"vs 20d_avg={int(sv_base['avg_short_volume']):,d}")
        else:
            print(f"  short_vol day={sv:,d} ratio={ratio:.2f}%  (no baseline)")
    else:
        print("  short_vol: no data")

    # 2) News
    news = fetch_news(ticker, dt_iso)
    if news:
        for n in news:
            print(f"  news [{n.get('sentiment','-')}] {n.get('publisher','?')}: {n['title'][:80]}")
    else:
        print("  news: none in +/- 30 min window")

    # 3) Float
    flt = fetch_float(ticker)
    float_shares = None
    pct_of_float = None
    if flt and "float_shares" in flt:
        float_shares = int(float(flt["float_shares"]))
        if float_shares > 0:
            pct_of_float = round(wick_vol / float_shares * 100, 6)
            print(f"  float={float_shares:,d}  wick_vol={wick_vol:,d}  "
                  f"= {pct_of_float}% of float")
        else:
            print(f"  float: {flt}")
    else:
        print(f"  float: {flt}")

    enriched = dict(w)
    enriched["short_volume"] = sv_summary
    enriched["news"] = news
    enriched["float_shares"] = float_shares
    enriched["wick_vol_pct_of_float"] = pct_of_float
    return enriched


def main():
    wicks = json.loads(PILOT_FILE.read_text())
    print(f"Loaded {len(wicks)} wicks from {PILOT_FILE}")
    out = [enrich_wick(w) for w in wicks]
    OUT_JSON.write_text(json.dumps(out, indent=2))
    # Flat CSV summary
    cols = [
        "ticker", "date", "time_et", "direction", "wick_pct", "ratio",
        "touched", "days_to_touch",
        "sv_day", "sv_total", "sv_ratio_pct", "sv_20d_avg", "sv_zscore",
        "news_count", "news_sentiment_summary",
        "float_shares", "wick_vol_pct_of_float",
    ]
    with OUT_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in out:
            sv = r.get("short_volume") or {}
            news = r.get("news") or []
            sentiments = [n.get("sentiment") for n in news if n.get("sentiment")]
            news_summary = ",".join(sentiments) if sentiments else ""
            w.writerow([
                r["ticker"], r["date"], r["time_et"], r["direction"],
                r["wick_pct"], r["ratio"], r["touched"], r.get("days_to_touch", ""),
                sv.get("short_volume", ""), sv.get("total_volume", ""),
                sv.get("short_volume_ratio_pct", ""),
                sv.get("20d_avg_short_volume", ""), sv.get("short_vol_zscore_simple", ""),
                len(news), news_summary,
                r.get("float_shares", ""), r.get("wick_vol_pct_of_float", ""),
            ])
    print(f"\nWrote {OUT_JSON}  and  {OUT_CSV}")


if __name__ == "__main__":
    main()
