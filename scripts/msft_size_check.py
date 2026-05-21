"""
Widen the search: every print at $415.50-$416.00 in the 13:30-14:00 window,
ordered by size descending. Make sure we're not missing a big block.
"""
import os, requests
from datetime import datetime, timezone, timedelta

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})
ET = timezone(timedelta(hours=-4))

d = datetime.strptime("2026-05-18", "%Y-%m-%d")
start = datetime(d.year, d.month, d.day, 13, 30, tzinfo=ET)
end   = datetime(d.year, d.month, d.day, 14,  0, tzinfo=ET)
u = f"{BASE}/v3/trades/MSFT"
p = {"timestamp.gte": int(start.timestamp() * 1e9),
     "timestamp.lt":  int(end.timestamp() * 1e9),
     "limit": 50000, "order": "asc"}
trades = []
while u:
    r = S.get(u, params=p, timeout=120)
    if r.status_code != 200: break
    j = r.json()
    for t in j.get("results", []):
        ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
        if not ts_ns: continue
        ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(ET)
        trades.append({"t": ts, "price": t["price"], "size": t.get("size", 0),
                       "exchange": t.get("exchange"), "conditions": t.get("conditions") or [],
                       "trf_id": t.get("trf_id")})
    u = j.get("next_url"); p = None

print(f"Total trades 13:30-14:00: {len(trades)}")

# All prints between $415.50 and $416.00
window = [t for t in trades if 415.50 <= t["price"] <= 416.00]
print(f"\nPrints at $415.50-$416.00 in 13:30-14:00: {len(window)}")
print(f"Total shares in that band: {sum(t['size'] for t in window)}")

# Largest 20 by size
print(f"\n=== Top 20 by SIZE (largest first) ===")
for t in sorted(window, key=lambda x: -x['size'])[:20]:
    print(f"  {t['t'].strftime('%H:%M:%S.%f')[:-3]}  ${t['price']:>8.4f}  sz={t['size']:>6}  ex={t['exchange']:>3}  cond={t['conditions']}  trf={t.get('trf_id')}")

# Aggregate by exact price
from collections import defaultdict
by_price = defaultdict(lambda: {"n": 0, "vol": 0})
for t in window:
    by_price[round(t['price'], 4)]["n"] += 1
    by_price[round(t['price'], 4)]["vol"] += t['size']
print(f"\n=== Aggregate by exact price ===")
for px, d in sorted(by_price.items()):
    print(f"  ${px:>8.4f}  {d['n']:>4} prints  {d['vol']:>7} shares  ${d['vol']*px:>14,.0f} notional")

# Anything below $416 outside the 13:45 batch?
batch = [t for t in window if t["t"].hour == 13 and t["t"].minute == 45 and t["t"].second == 25]
non_batch = [t for t in window if not (t["t"].hour == 13 and t["t"].minute == 45 and t["t"].second == 25)]
print(f"\n=== Window breakdown ===")
print(f"  13:45:25 batch prints: {len(batch)}  ({sum(t['size'] for t in batch)} shares)")
print(f"  Other prints in $415.50-$416.00 (any time 13:30-14:00): {len(non_batch)}  ({sum(t['size'] for t in non_batch)} shares)")
if non_batch:
    print(f"\nNon-batch low prints (top 10 by size):")
    for t in sorted(non_batch, key=lambda x: -x['size'])[:10]:
        print(f"  {t['t'].strftime('%H:%M:%S.%f')[:-3]}  ${t['price']:>8.4f}  sz={t['size']:>5}  ex={t['exchange']:>3}  cond={t['conditions']}")
