"""
Ghost wick backtester for TSLA, 2 years.

Strategy:
1. For each trading day, pull 1-min aggregates (cheap)
2. For each day, also pull all trades and filter for TRF prints
   (trades with trf_id set, indicating off-exchange via TRF)
3. For each TRF print, compare to the lit price (minute aggregate O/H/L/C average)
   for that same minute
4. A "ghost wick" = TRF print where |price - lit_price| / lit_price > 1%
   (i.e. the off-exchange print is >1% away from where the stock was trading)
5. For each ghost wick, look forward up to 10 trading days. Did regular price
   touch the ghost print level?

Output:
- ghost_wicks.csv: one row per ghost wick with date, time, ghost_price,
  lit_price, direction (up/down), days_to_touch, touched_within_10d
- summary.txt: hit rate, avg days to touch, breakdowns

To keep runtime manageable: ~6 months of TSLA at a time.
This script runs for a configurable date range via env vars.
"""
import os, requests, time, csv, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

TICKER = "TSLA"
START = os.environ.get("START_DATE", "2025-11-19")  # 6 months back default
END   = os.environ.get("END_DATE",   "2026-05-19")
WICK_THRESHOLD_PCT = float(os.environ.get("WICK_PCT", "1.0"))  # 1% off lit price
MIN_SIZE = int(os.environ.get("MIN_SIZE", "10"))  # ignore tiny odd lots
LOOKFORWARD_DAYS = 10

print(f"=== Ghost wick backtest ===")
print(f"  Ticker: {TICKER}")
print(f"  Date range: {START} to {END}")
print(f"  Wick threshold: {WICK_THRESHOLD_PCT}% from lit")
print(f"  Min trade size: {MIN_SIZE}")
print(f"  Look-forward: {LOOKFORWARD_DAYS} trading days")
print()

def trading_days(start_str, end_str):
    """Get trading days from polygon aggregates."""
    r = S.get(f"{BASE}/v2/aggs/ticker/{TICKER}/range/1/day/{start_str}/{end_str}",
              params={"limit": 5000}, timeout=30)
    return [datetime.fromtimestamp(d['t']/1000, tz=timezone.utc).date()
            for d in r.json().get("results", [])]

days = trading_days(START, END)
print(f"Trading days: {len(days)}")

# Pre-fetch daily bars for hit detection
day_bars = {}
r = S.get(f"{BASE}/v2/aggs/ticker/{TICKER}/range/1/day/{START}/2026-12-31",
          params={"limit": 5000}, timeout=30)
for d in r.json().get("results", []):
    dt = datetime.fromtimestamp(d['t']/1000, tz=timezone.utc).date()
    day_bars[dt] = {"o": d['o'], "h": d['h'], "l": d['l'], "c": d['c'], "v": d['v']}
print(f"Daily bars cached: {len(day_bars)}")

ghost_wicks = []

for day_idx, day in enumerate(days):
    # Pull 1-min aggregates for this day
    day_str = day.strftime("%Y-%m-%d")
    r = S.get(f"{BASE}/v2/aggs/ticker/{TICKER}/range/1/minute/{day_str}/{day_str}",
              params={"limit": 50000}, timeout=30)
    mins = r.json().get("results", [])

    # Build minute_idx (ET) -> mid price
    minute_mid = {}
    for m in mins:
        t = datetime.fromtimestamp(m['t']/1000, tz=timezone.utc).astimezone(timezone(timedelta(hours=-4)))
        key = (t.hour, t.minute)
        minute_mid[key] = (m['o'] + m['c']) / 2  # mid of open/close

    # Pull all trades for regular hours (9:30 - 16:00 ET)
    start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=timezone(timedelta(hours=-4)))
    end   = datetime(day.year, day.month, day.day, 16,  0, tzinfo=timezone(timedelta(hours=-4)))
    start_ns = int(start.timestamp() * 1e9)
    end_ns   = int(end.timestamp() * 1e9)

    u = f"{BASE}/v3/trades/{TICKER}"
    p = {"timestamp.gte": start_ns, "timestamp.lt": end_ns, "limit": 50000, "order": "asc"}

    day_ghosts = 0
    pages = 0
    total_trades = 0
    trf_trades = 0
    while u and pages < 200:
        for attempt in range(5):
            try:
                r = S.get(u, params=p if pages == 0 else None, timeout=120); break
            except Exception:
                time.sleep(1 + attempt)
        if r.status_code != 200:
            print(f"  {day_str} page {pages} HTTP {r.status_code}")
            break
        j = r.json()
        for t in j.get("results", []):
            total_trades += 1
            # Filter for TRF prints (off-exchange)
            if "trf_id" not in t:
                continue
            # Exclude mechanical/aggregated prints that don't represent directional flow:
            #   2  = Average Price Trade (VWAP fill, reported all day)
            #   12 = Form T (extended hours, not regular session)
            #   16 = Stopped Stock
            #   33 = Sold (Out of Sequence)
            #   52, 53 = Derivatively Priced / Re-Opening (auction)
            conds = set(t.get("conditions") or [])
            if conds & {2, 12, 16, 33, 52, 53}:
                continue
            trf_trades += 1
            if t.get("size", 0) < MIN_SIZE:
                continue
            price = t.get("price", 0)
            ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
            if not ts_ns:
                continue
            ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(timezone(timedelta(hours=-4)))
            key = (ts.hour, ts.minute)
            lit = minute_mid.get(key)
            if not lit:
                continue
            diff_pct = abs(price - lit) / lit * 100
            if diff_pct >= WICK_THRESHOLD_PCT:
                direction = "down" if price < lit else "up"
                ghost_wicks.append({
                    "date": day_str,
                    "time_et": ts.strftime("%H:%M:%S.%f")[:-3],
                    "ghost_price": price,
                    "lit_price": round(lit, 4),
                    "diff_pct": round(diff_pct, 3),
                    "direction": direction,
                    "size": t.get("size"),
                    "exchange": t.get("exchange"),
                    "conditions": str(t.get("conditions")),
                    "trf_id": t.get("trf_id"),
                })
                day_ghosts += 1
        u = j.get("next_url"); p = None; pages += 1
    if day_idx % 10 == 0 or day_ghosts > 0:
        print(f"  {day_str}: {total_trades} trades, {trf_trades} TRF, {day_ghosts} ghost wicks")

print(f"\nTotal ghost wicks found: {len(ghost_wicks)}")

# Now score each: did regular price touch the ghost price within N days?
print(f"\nScoring hits (within {LOOKFORWARD_DAYS} trading days)...")
day_index = {d: i for i, d in enumerate(days)}
for w in ghost_wicks:
    d = datetime.strptime(w["date"], "%Y-%m-%d").date()
    if d not in day_index:
        w["touched"] = None
        w["days_to_touch"] = None
        continue
    start_i = day_index[d]
    target = w["ghost_price"]
    direction = w["direction"]
    touched_day = None
    for offset in range(1, LOOKFORWARD_DAYS + 1):
        if start_i + offset >= len(days):
            break
        fwd_day = days[start_i + offset]
        bar = day_bars.get(fwd_day)
        if not bar:
            continue
        if direction == "down" and bar["l"] <= target:
            touched_day = offset
            break
        elif direction == "up" and bar["h"] >= target:
            touched_day = offset
            break
    w["touched"] = touched_day is not None
    w["days_to_touch"] = touched_day

# Write CSV
out = f"data/ghost_wicks_{TICKER}.csv"
os.makedirs("data", exist_ok=True)
if ghost_wicks:
    with open(out, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(ghost_wicks[0].keys()))
        wr.writeheader()
        wr.writerows(ghost_wicks)
    print(f"Wrote {out}")

# Summary
print(f"\n=== SUMMARY ===")
print(f"Total ghost wicks: {len(ghost_wicks)}")
touched = [w for w in ghost_wicks if w.get("touched")]
print(f"Touched within {LOOKFORWARD_DAYS}d: {len(touched)} ({100*len(touched)/max(len(ghost_wicks),1):.1f}%)")
if touched:
    avg_days = sum(w["days_to_touch"] for w in touched) / len(touched)
    print(f"Avg days to touch (when hit): {avg_days:.1f}")

# Break out by direction
for d in ["up", "down"]:
    sub = [w for w in ghost_wicks if w["direction"] == d]
    sub_t = [w for w in sub if w.get("touched")]
    print(f"  {d}-wicks: {len(sub)} total, {len(sub_t)} touched ({100*len(sub_t)/max(len(sub),1):.1f}%)")

# Break by size tier
for tier in [(10,99), (100,999), (1000,9999), (10000,1_000_000)]:
    sub = [w for w in ghost_wicks if tier[0] <= w["size"] <= tier[1]]
    sub_t = [w for w in sub if w.get("touched")]
    if sub:
        print(f"  size {tier[0]}-{tier[1]}: {len(sub)} total, {len(sub_t)} touched ({100*len(sub_t)/len(sub):.1f}%)")

# Break by % deviation tier
for tier in [(1, 2), (2, 5), (5, 10), (10, 100)]:
    sub = [w for w in ghost_wicks if tier[0] <= w["diff_pct"] < tier[1]]
    sub_t = [w for w in sub if w.get("touched")]
    if sub:
        print(f"  diff {tier[0]}-{tier[1]}%: {len(sub)} total, {len(sub_t)} touched ({100*len(sub_t)/len(sub):.1f}%)")
