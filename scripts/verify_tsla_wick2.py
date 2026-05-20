"""
Widen the search: pull ALL TSLA trades 5/19 and find any print below $400.
Also pull the full day OHLC from /v2/aggs to see what Polygon/Massive's
official daily low is.
"""
import os, requests, time, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

# Full day 5/19/2026 ET (regular hours + extended)
# Pre-market 4am ET to post-close 8pm ET
# 4am ET = 8 UTC, 8pm ET = 0 UTC next day
start = datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc)
end   = datetime(2026, 5, 20, 1, 0, 0, tzinfo=timezone.utc)
start_ns = int(start.timestamp() * 1e9)
end_ns   = int(end.timestamp() * 1e9)

print(f"Pulling ALL TSLA trades {start.isoformat()} to {end.isoformat()} UTC")
print()

# First check daily aggregate
r = S.get(f"{BASE}/v2/aggs/ticker/TSLA/range/1/day/2026-05-19/2026-05-19", timeout=30)
print("=== Daily aggregate (TSLA 5/19) ===")
print(json.dumps(r.json(), indent=2))
print()

# Now get the minute aggregate around 13:46 ET (17:46 UTC)
r = S.get(f"{BASE}/v2/aggs/ticker/TSLA/range/1/minute/2026-05-19/2026-05-19",
          params={"limit": 50000}, timeout=30)
j = r.json()
mins = j.get("results", [])
print(f"=== Minute bars (count={len(mins)}) — show any with low < $400 ===")
low_mins = [m for m in mins if m.get("l", 9999) < 400]
print(f"Found {len(low_mins)} minute bars with low < $400")
for m in low_mins:
    t = datetime.fromtimestamp(m["t"]/1000, tz=timezone.utc)
    et = t.astimezone(timezone(timedelta(hours=-4)))
    print(f"  ET {et.strftime('%H:%M')}  O={m['o']:.2f} H={m['h']:.2f} L={m['l']:.2f} C={m['c']:.2f} V={m['v']}")

# Also print 13:40-13:50 ET minute bars to compare to user's screenshot
print(f"\n=== Minute bars 13:40-13:50 ET (compare to ToS) ===")
for m in mins:
    t = datetime.fromtimestamp(m["t"]/1000, tz=timezone.utc)
    et = t.astimezone(timezone(timedelta(hours=-4)))
    if et.hour == 13 and 40 <= et.minute <= 50:
        print(f"  ET {et.strftime('%H:%M')}  O={m['o']:.2f} H={m['h']:.2f} L={m['l']:.2f} C={m['c']:.2f} V={m['v']}")

# Now scan ALL trades for prints below 400
print(f"\n=== Scanning ALL trades for prints < $400 ===")
u = f"{BASE}/v3/trades/TSLA"
p = {"timestamp.gte": start_ns, "timestamp.lt": end_ns, "limit": 50000, "order": "asc"}
total = 0
low_prints = []
pages = 0
while u and pages < 500:
    for attempt in range(5):
        try:
            r = S.get(u, params=p if pages == 0 else None, timeout=60)
            break
        except Exception as e:
            time.sleep(1 + attempt)
    if r.status_code != 200:
        print(f"HTTP {r.status_code}: {r.text[:200]}")
        break
    j = r.json()
    results = j.get("results", [])
    total += len(results)
    for t in results:
        if t.get("price", 9999) < 400:
            low_prints.append(t)
    u = j.get("next_url")
    p = None
    pages += 1
    if pages % 20 == 0:
        print(f"  page {pages}, total trades: {total}, low prints: {len(low_prints)}")

print(f"\nTotal trades scanned: {total}")
print(f"Total prints below $400: {len(low_prints)}")

if low_prints:
    print("\n=== Lowest prints ===")
    low_prints.sort(key=lambda x: x.get("price", 0))
    for t in low_prints[:30]:
        ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
        ts = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc) if ts_ns else None
        et = ts.astimezone(timezone(timedelta(hours=-4))) if ts else None
        print(f"  ${t.get('price'):.3f}  size={t.get('size')}  exch={t.get('exchange')}  "
              f"conds={t.get('conditions')}  ET={et.strftime('%H:%M:%S.%f')[:-3] if et else '?'}")
