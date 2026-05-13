"""
Dark Pool Ratio (DPR) pipeline for TradeStation.

For each ticker in tickers.csv, pulls the last N trading days of trade-level data
from Massive's /v3/trades endpoint, classifies each trade as lit vs dark
(exchange==4 AND trf_id present), and emits:

  data/{TICKER}_DPR_TS.txt          - daily dark pool % (all dark trades)
  data/{TICKER}_DPR_BLOCKS_TS.txt   - daily dark pool % weighted by notional, blocks only
  data/{TICKER}_DPR_CARTERET_TS.txt - daily Carteret TRF (202) share of total notional
  data/{TICKER}_DPR_components.csv  - full daily breakdown (lit/dark/blocks/TRF venues)

Files are TradeStation ASCII format:
  Date,Time,Open,High,Low,Close,Volume
  Open=High=Low=Close=DPR_value (so it plots as a flat dot per day)
  Volume=total daily notional in $millions (informational)
"""
import os
import sys
import csv
import time
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
DAYS_BACK = int(os.environ.get("DPR_DAYS_BACK", "60"))
BLOCK_NOTIONAL_THRESHOLD = 100_000  # $100k = block (Massive's definition)

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
DATA.mkdir(exist_ok=True)

S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})


def load_tickers():
    tickers = []
    with open(REPO / "tickers.csv") as f:
        for line in f:
            t = line.strip().split(",")[0].strip().upper()
            if t and t != "TICKER":
                tickers.append(t)
    return tickers


def trading_days(n_back):
    """Return list of weekday dates ending yesterday, going n_back trading days."""
    out = []
    d = datetime.now(timezone.utc).date() - timedelta(days=1)
    while len(out) < n_back:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def fetch_trades_for_day(ticker, day):
    """Yield each trade dict for one trading day. Handles pagination."""
    start_ns = int(datetime.combine(day, datetime.min.time(), timezone.utc).timestamp() * 1e9)
    end_ns = int(datetime.combine(day + timedelta(days=1), datetime.min.time(), timezone.utc).timestamp() * 1e9)
    url = f"{BASE}/v3/trades/{ticker}"
    params = {
        "timestamp.gte": start_ns,
        "timestamp.lt": end_ns,
        "limit": 50000,
        "order": "asc",
    }
    while url:
        for attempt in range(4):
            try:
                r = S.get(url, params=params if "timestamp.gte" not in (url or "") else None, timeout=90)
                break
            except requests.RequestException as e:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        if r.status_code == 429:
            time.sleep(5)
            continue
        if r.status_code != 200:
            print(f"  ! {ticker} {day} HTTP {r.status_code}: {r.text[:200]}", flush=True)
            return
        j = r.json()
        for tr in j.get("results", []):
            yield tr
        url = j.get("next_url")
        params = None  # next_url already has all params


def classify_day(ticker, day):
    """Return dict of daily stats for one ticker/day."""
    total_n = 0
    total_notional = 0.0
    dark_n = 0
    dark_notional = 0.0
    block_total_notional = 0.0
    block_dark_notional = 0.0
    venue_notional = {201: 0.0, 202: 0.0, 203: 0.0}

    for tr in fetch_trades_for_day(ticker, day):
        size = tr.get("size") or 0
        price = tr.get("price") or 0
        notional = size * price
        if size == 0 or price == 0:
            continue
        total_n += 1
        total_notional += notional

        is_dark = (tr.get("exchange") == 4) and (tr.get("trf_id") is not None)
        if is_dark:
            dark_n += 1
            dark_notional += notional
            trf = tr.get("trf_id")
            if trf in venue_notional:
                venue_notional[trf] += notional

        if notional >= BLOCK_NOTIONAL_THRESHOLD:
            block_total_notional += notional
            if is_dark:
                block_dark_notional += notional

    return {
        "date": day,
        "trades": total_n,
        "notional": total_notional,
        "dark_trades": dark_n,
        "dark_notional": dark_notional,
        "block_notional": block_total_notional,
        "block_dark_notional": block_dark_notional,
        "trf_201": venue_notional[201],
        "trf_202": venue_notional[202],
        "trf_203": venue_notional[203],
    }


def write_ts_file(path, rows, value_fn):
    """Write TradeStation ASCII file. rows is list of day dicts."""
    with open(path, "w") as f:
        f.write("Date,Time,Open,High,Low,Close,Volume\n")
        for r in rows:
            v = value_fn(r)
            if v is None:
                continue
            dt = r["date"].strftime("%m/%d/%Y")
            notional_m = int(r["notional"] / 1_000_000)  # millions
            f.write(f"{dt},1600,{v:.4f},{v:.4f},{v:.4f},{v:.4f},{notional_m}\n")


def main():
    tickers = load_tickers()
    days = trading_days(DAYS_BACK)
    print(f"Processing {len(tickers)} tickers over {len(days)} trading days "
          f"({days[0]} → {days[-1]})", flush=True)

    for ticker in tickers:
        print(f"\n=== {ticker} ===", flush=True)
        t0 = time.time()
        rows = []
        for i, day in enumerate(days):
            try:
                stats = classify_day(ticker, day)
            except Exception as e:
                print(f"  ! {ticker} {day} failed: {e}", flush=True)
                continue
            rows.append(stats)
            if (i + 1) % 10 == 0 or i == len(days) - 1:
                print(f"  {ticker} {day}: trades={stats['trades']:,}  dark%={(stats['dark_notional']/stats['notional']*100) if stats['notional'] else 0:.2f}", flush=True)

        # Filter days with actual data
        rows = [r for r in rows if r["notional"] > 0]
        if not rows:
            print(f"  ! No data for {ticker}, skipping", flush=True)
            continue

        # 1) Daily DPR by notional
        write_ts_file(
            DATA / f"{ticker}_DPR_TS.txt",
            rows,
            lambda r: (r["dark_notional"] / r["notional"] * 100) if r["notional"] else 0,
        )

        # 2) Block DPR
        write_ts_file(
            DATA / f"{ticker}_DPR_BLOCKS_TS.txt",
            rows,
            lambda r: (r["block_dark_notional"] / r["block_notional"] * 100) if r["block_notional"] else 0,
        )

        # 3) Carteret share of total $
        write_ts_file(
            DATA / f"{ticker}_DPR_CARTERET_TS.txt",
            rows,
            lambda r: (r["trf_202"] / r["notional"] * 100) if r["notional"] else 0,
        )

        # 4) Components CSV (research/inspection)
        with open(DATA / f"{ticker}_DPR_components.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "trades", "notional_$", "dark_trades", "dark_notional_$",
                        "dark_pct_by_notional", "block_notional_$", "block_dark_notional_$",
                        "block_dark_pct", "trf_201_NYSE_$", "trf_202_Carteret_$",
                        "trf_203_Chicago_$", "carteret_pct_of_total"])
            for r in rows:
                w.writerow([
                    r["date"].isoformat(),
                    r["trades"],
                    f"{r['notional']:.0f}",
                    r["dark_trades"],
                    f"{r['dark_notional']:.0f}",
                    f"{(r['dark_notional']/r['notional']*100):.4f}" if r["notional"] else "0",
                    f"{r['block_notional']:.0f}",
                    f"{r['block_dark_notional']:.0f}",
                    f"{(r['block_dark_notional']/r['block_notional']*100):.4f}" if r["block_notional"] else "0",
                    f"{r['trf_201']:.0f}",
                    f"{r['trf_202']:.0f}",
                    f"{r['trf_203']:.0f}",
                    f"{(r['trf_202']/r['notional']*100):.4f}" if r["notional"] else "0",
                ])

        print(f"  ✓ {ticker} done in {time.time()-t0:.1f}s ({len(rows)} days written)", flush=True)


if __name__ == "__main__":
    main()
