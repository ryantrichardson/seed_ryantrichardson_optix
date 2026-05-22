"""
Tiny pilot: scan NVDA, TSLA, AMD for the past 6 months for "average price"
ghost bars and check forward returns.

Signal definition (per the NVDA 5/21 16:30 case):
  - Single trade with cond 22 (average price)
  - On exchange 4 (TRF / off-exchange)
  - Size >= 500 shares
  - Notional >= $100k
  - Print price differs from surrounding 1-min VWAP by >= 1.0%

For each detected signal, record:
  - ticker, datetime_et, price, size, notional
  - direction: 'UP' if print >= 1% above surrounding price (= seller finished),
               'DOWN' if print <= 1% below surrounding price (= buyer finished)
  - forward returns: close at +1d, +3d, +5d vs print day's close
  - hit if direction='UP' -> forward return is NEGATIVE
       if direction='DOWN' -> forward return is POSITIVE
"""
import os, sys, requests, csv, json
from datetime import datetime, timezone, timedelta, date
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})
ET = timezone(timedelta(hours=-4))

TICKER = os.environ.get("TICKER", "NVDA")
DAYS_BACK = int(os.environ.get("DAYS_BACK", "180"))
END_DATE = os.environ.get("END_DATE", "2026-05-21")

end = datetime.strptime(END_DATE, "%Y-%m-%d").date()
start = end - timedelta(days=DAYS_BACK)

print(f"=== {TICKER} cond-22 pilot scan: {start} to {end} ({DAYS_BACK} days) ===")

# Step 1: fetch daily bars (for forward-return computation later)
print(f"\n[1/3] Fetching daily bars...")
u = f"{BASE}/v2/aggs/ticker/{TICKER}/range/1/day/{start}/{end}"
r = S.get(u, params={"limit": 5000, "adjusted": "true"}, timeout=60)
daily = []
if r.status_code == 200:
    for b in r.json().get("results", []):
        d = datetime.fromtimestamp(b["t"]/1000, tz=ET).date()
        daily.append({"d": d, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]})
print(f"  got {len(daily)} daily bars")
daily_by_date = {b["d"]: b for b in daily}
dates_sorted = sorted(daily_by_date.keys())

def forward_close(d, n):
    """Get close n trading days after d."""
    if d not in dates_sorted: return None
    idx = dates_sorted.index(d)
    if idx + n >= len(dates_sorted): return None
    return daily_by_date[dates_sorted[idx + n]]["c"]

def forward_extreme(d, n, direction):
    """Get most extreme price (low if direction='UP', high if 'DOWN') across n forward days."""
    if d not in dates_sorted: return None
    idx = dates_sorted.index(d)
    bars = [daily_by_date[dates_sorted[i]] for i in range(idx+1, min(idx+1+n, len(dates_sorted)))]
    if not bars: return None
    if direction == "UP":
        return min(b["l"] for b in bars)
    else:
        return max(b["h"] for b in bars)

# Step 2: scan trades day-by-day for cond-22 candidates
print(f"\n[2/3] Scanning trade tape for cond-22 prints (this is the slow part)...")
candidates = []

for trading_day in dates_sorted:
    # Narrow window: 15:50 RTH through 16:35 AH only. cond 22 prints reliably cluster
    # around the regular session close because that's when VWAP fills get booked out.
    # This cuts the trade volume per day from ~3-5M down to ~50-150k.
    day_start = datetime.combine(trading_day, datetime.min.time(), tzinfo=ET).replace(hour=15, minute=50)
    day_end   = datetime.combine(trading_day, datetime.min.time(), tzinfo=ET).replace(hour=16, minute=35)
    u = f"{BASE}/v3/trades/{TICKER}"
    p = {"timestamp.gte": int(day_start.timestamp() * 1e9),
         "timestamp.lt":  int(day_end.timestamp() * 1e9),
         "conditions.any_of": "22",  # try server-side filter first
         "limit": 50000, "order": "asc"}
    day_cands = []
    pages = 0
    next_url = u
    next_params = p
    while next_url and pages < 50:
        try:
            r = S.get(next_url, params=next_params, timeout=120)
        except Exception as e:
            print(f"  {trading_day} ERROR: {e}")
            break
        if r.status_code != 200:
            # Server-side filter may not be supported; fall back to client-side scan
            if pages == 0 and "conditions" in str(p):
                p2 = {"timestamp.gte": p["timestamp.gte"], "timestamp.lt": p["timestamp.lt"],
                      "limit": 50000, "order": "asc"}
                next_params = p2
                next_url = u
                p = p2
                continue
            print(f"  {trading_day} HTTP {r.status_code}")
            break
        j = r.json()
        for t in j.get("results", []):
            conds = t.get("conditions") or []
            if 22 not in conds: continue
            size = t.get("size", 0)
            if size < 500: continue
            if t.get("exchange") != 4: continue
            price = t["price"]
            notional = price * size
            if notional < 100_000: continue
            ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
            ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(ET)
            day_cands.append({
                "ticker": TICKER,
                "datetime_et": ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "date": ts.date().isoformat(),
                "price": price,
                "size": size,
                "notional": round(notional, 0),
                "exchange": t.get("exchange"),
                "conditions": conds,
                "trf_id": t.get("trf_id"),
            })
        next_url = j.get("next_url")
        next_params = None
        pages += 1
    if day_cands:
        # Determine "surrounding price" using the trading day's close
        close = daily_by_date[trading_day]["c"]
        for c in day_cands:
            pct = (c["price"] - close) / close * 100
            c["pct_from_close"] = round(pct, 3)
            if pct >= 1.0:
                c["direction"] = "UP"
            elif pct <= -1.0:
                c["direction"] = "DOWN"
            else:
                c["direction"] = "neutral"
            # Add forward returns
            d = trading_day
            c["close_d0"] = close
            c["close_d1"] = forward_close(d, 1)
            c["close_d3"] = forward_close(d, 3)
            c["close_d5"] = forward_close(d, 5)
            c["fwd_extreme_5d"] = forward_extreme(d, 5, c["direction"]) if c["direction"] in ("UP","DOWN") else None
            # Compute returns
            for n in [1,3,5]:
                fc = c.get(f"close_d{n}")
                c[f"ret_d{n}_pct"] = round((fc-close)/close*100, 3) if fc else None
            # Hit logic
            if c["direction"] == "UP":   # client sold -> expect down move
                c["hit_d5"] = (c["ret_d5_pct"] is not None and c["ret_d5_pct"] < 0)
            elif c["direction"] == "DOWN": # client bought -> expect up move
                c["hit_d5"] = (c["ret_d5_pct"] is not None and c["ret_d5_pct"] > 0)
            else:
                c["hit_d5"] = None
        candidates.extend(day_cands)
        for c in day_cands:
            if c["direction"] in ("UP","DOWN"):
                print(f"  {c['datetime_et']}  ${c['price']:>9.4f}  sz={c['size']:>5}  "
                      f"notional=${c['notional']:>11,.0f}  pct_from_close={c['pct_from_close']:+.2f}%  "
                      f"dir={c['direction']:4}  ret_d5={c.get('ret_d5_pct')}%")

print(f"\n[3/3] Total candidates: {len(candidates)}")
directional = [c for c in candidates if c["direction"] in ("UP","DOWN")]
print(f"Directional (>= 1% from close): {len(directional)}")
if directional:
    hits = sum(1 for c in directional if c.get("hit_d5"))
    print(f"Hit rate (5d): {hits}/{len(directional)} = {hits/len(directional)*100:.1f}%")
    # Mean return in expected direction
    rets = []
    for c in directional:
        r = c.get("ret_d5_pct")
        if r is None: continue
        # Flip sign for UP (we want negative move to be a "positive" outcome)
        rets.append(-r if c["direction"]=="UP" else r)
    if rets:
        print(f"Mean signed forward return (favorable direction): {sum(rets)/len(rets):+.3f}%")
        print(f"Median:  {sorted(rets)[len(rets)//2]:+.3f}%")

# Write CSV
out_path = f"data/cond22_pilot_{TICKER}.csv"
os.makedirs("data", exist_ok=True)
if candidates:
    keys = list(candidates[0].keys())
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for c in candidates:
            row = {**c}
            if isinstance(row.get("conditions"), list): row["conditions"] = "|".join(str(x) for x in row["conditions"])
            w.writerow(row)
    print(f"\nWrote {out_path}")
else:
    print("\nNo candidates found.")
