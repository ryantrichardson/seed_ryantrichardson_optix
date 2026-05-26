"""
TSLA 14-wick screenshot backtest.

For each wick:
  - Fetch 1-min bars from wick_time -> wick_time + 25 calendar days
  - Determine if the wick extreme was reached:
      * Same-day after wick (counts only if AFTER wick minute)
      * Within 10 trading days
      * Within 20 trading days
  - Extended hours bars count (per user instruction).

Output: data/tsla_14_wicks_results.csv
"""
import os, sys, json, time
from pathlib import Path
from datetime import datetime, timedelta, timezone, date
from urllib.parse import urlencode
import requests

API_KEY = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
ET = timezone(timedelta(hours=-4))  # EDT (May = DST)

INFILE = Path("data/tsla_14_wicks.json")
OUTFILE = Path("data/tsla_14_wicks_results.csv")


def http_get(path: str, params: dict) -> dict:
    params = {**params, "apiKey": API_KEY}
    url = f"{BASE}{path}?{urlencode(params)}"
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    return {}


def fetch_minute_bars(ticker: str, start_date: str, end_date: str) -> list:
    """Fetch 1-min bars (extended hours included via adjusted=true; we'll filter session in client)."""
    path = f"/v2/aggs/ticker/{ticker}/range/1/minute/{start_date}/{end_date}"
    all_bars = []
    next_url = None
    params = {"adjusted": "true", "sort": "asc", "limit": 50000}
    while True:
        if next_url:
            # next_url already has params
            url = next_url + f"&apiKey={API_KEY}"
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            data = r.json()
        else:
            data = http_get(path, params)
        results = data.get("results") or []
        all_bars.extend(results)
        next_url = data.get("next_url")
        if not next_url:
            break
    return all_bars


def add_trading_days(start: date, n: int) -> date:
    """Crude trading-day calendar: skip Sat/Sun (no holiday calendar — close enough for window sizing)."""
    d = start
    added = 0
    while added < n:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def main():
    wicks = json.loads(INFILE.read_text())["wicks"]
    rows = []
    for w in wicks:
        wid = w["id"]
        ticker = w["ticker"]
        direction = w["direction"]
        extreme = float(w["extreme"])

        # Parse wick datetime (ET)
        dt_et = datetime.strptime(f"{w['date']} {w['time_et']}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
        wick_minute_ms = int(dt_et.timestamp() * 1000)

        wick_day = dt_et.date()
        end_day = add_trading_days(wick_day, 25)  # buffer for 20-day window

        print(f"[{wid}] {ticker} {w['date']} {w['time_et']} {direction}->{extreme} ({w['body_color']}, {w['session']})", file=sys.stderr)
        bars = fetch_minute_bars(ticker, wick_day.isoformat(), end_day.isoformat())
        print(f"      {len(bars)} bars fetched", file=sys.stderr)

        same_day_after_hit = False
        same_day_after_hit_dt = None
        hit_10d = False
        hit_10d_dt = None
        hit_10d_bars_to_hit = None
        hit_20d = False
        hit_20d_dt = None
        hit_20d_bars_to_hit = None

        # Compute trading-day boundaries
        end_10d_dt = datetime.combine(add_trading_days(wick_day, 10), datetime.max.time()).replace(tzinfo=ET)
        end_20d_dt = datetime.combine(add_trading_days(wick_day, 20), datetime.max.time()).replace(tzinfo=ET)
        wick_day_end = datetime.combine(wick_day, datetime.max.time()).replace(tzinfo=ET)

        bars_counted = 0
        for b in bars:
            t_ms = b["t"]
            if t_ms <= wick_minute_ms:
                continue  # only bars AFTER the wick minute
            bars_counted += 1
            bar_dt = datetime.fromtimestamp(t_ms / 1000, tz=ET)
            hi = float(b["h"])
            lo = float(b["l"])

            hit = (direction == "up"   and hi >= extreme) or \
                  (direction == "down" and lo <= extreme)
            if not hit:
                continue

            # Same-day check
            if bar_dt <= wick_day_end and not same_day_after_hit:
                same_day_after_hit = True
                same_day_after_hit_dt = bar_dt.strftime("%Y-%m-%d %H:%M ET")

            # 10-day check
            if bar_dt <= end_10d_dt and not hit_10d:
                hit_10d = True
                hit_10d_dt = bar_dt.strftime("%Y-%m-%d %H:%M ET")
                hit_10d_bars_to_hit = bars_counted

            # 20-day check
            if bar_dt <= end_20d_dt and not hit_20d:
                hit_20d = True
                hit_20d_dt = bar_dt.strftime("%Y-%m-%d %H:%M ET")
                hit_20d_bars_to_hit = bars_counted
                break  # once we hit 20d we have everything

        rows.append({
            "id": wid,
            "ticker": ticker,
            "date": w["date"],
            "time_et": w["time_et"],
            "direction": direction,
            "body_color": w["body_color"],
            "session": w["session"],
            "extreme": extreme,
            "wick_open": w["open"],
            "wick_close": w["close"],
            "wick_high": w["high"],
            "wick_low": w["low"],
            "same_day_after_hit": same_day_after_hit,
            "same_day_after_hit_dt": same_day_after_hit_dt or "",
            "hit_10d": hit_10d,
            "hit_10d_dt": hit_10d_dt or "",
            "hit_10d_bars_to_hit": hit_10d_bars_to_hit if hit_10d_bars_to_hit is not None else "",
            "hit_20d": hit_20d,
            "hit_20d_dt": hit_20d_dt or "",
            "hit_20d_bars_to_hit": hit_20d_bars_to_hit if hit_20d_bars_to_hit is not None else "",
            "bars_fetched": len(bars),
        })

    # Write CSV
    import csv
    OUTFILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTFILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {OUTFILE} ({len(rows)} rows)", file=sys.stderr)

    # Print summary to stdout
    n = len(rows)
    h10 = sum(1 for r in rows if r["hit_10d"])
    h20 = sum(1 for r in rows if r["hit_20d"])
    print(f"\n=== SUMMARY ===")
    print(f"Total wicks: {n}")
    print(f"10-day hit rate: {h10}/{n} = {h10/n*100:.1f}%")
    print(f"20-day hit rate: {h20}/{n} = {h20/n*100:.1f}%")

    # By body color
    print(f"\n=== BY BODY COLOR ===")
    for color in ["green", "red"]:
        sub = [r for r in rows if r["body_color"] == color]
        if not sub:
            continue
        h10c = sum(1 for r in sub if r["hit_10d"])
        h20c = sum(1 for r in sub if r["hit_20d"])
        print(f"{color}: n={len(sub)}  10d={h10c}/{len(sub)} ({h10c/len(sub)*100:.0f}%)  20d={h20c}/{len(sub)} ({h20c/len(sub)*100:.0f}%)")

    # By direction
    print(f"\n=== BY DIRECTION ===")
    for d in ["up", "down"]:
        sub = [r for r in rows if r["direction"] == d]
        if not sub:
            continue
        h10c = sum(1 for r in sub if r["hit_10d"])
        h20c = sum(1 for r in sub if r["hit_20d"])
        print(f"{d}: n={len(sub)}  10d={h10c}/{len(sub)} ({h10c/len(sub)*100:.0f}%)  20d={h20c}/{len(sub)} ({h20c/len(sub)*100:.0f}%)")

    # By session
    print(f"\n=== BY SESSION ===")
    for s in ["rth", "pre", "post"]:
        sub = [r for r in rows if r["session"] == s]
        if not sub:
            continue
        h10c = sum(1 for r in sub if r["hit_10d"])
        h20c = sum(1 for r in sub if r["hit_20d"])
        print(f"{s}: n={len(sub)}  10d={h10c}/{len(sub)} ({h10c/len(sub)*100:.0f}%)  20d={h20c}/{len(sub)} ({h20c/len(sub)*100:.0f}%)")

    # Cross-bucket: down-wicks by color
    print(f"\n=== DOWN-WICKS by body color ===")
    for color in ["green", "red"]:
        sub = [r for r in rows if r["direction"] == "down" and r["body_color"] == color]
        if not sub:
            continue
        h10c = sum(1 for r in sub if r["hit_10d"])
        h20c = sum(1 for r in sub if r["hit_20d"])
        print(f"down/{color}: n={len(sub)}  10d={h10c}/{len(sub)} ({h10c/len(sub)*100:.0f}%)  20d={h20c}/{len(sub)} ({h20c/len(sub)*100:.0f}%)")


if __name__ == "__main__":
    main()
