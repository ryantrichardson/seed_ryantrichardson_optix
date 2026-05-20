"""
Find the ONE print at $394.5-$394.8 that occurred at 14:46 ET.
Show all its conditions, exchange, timestamps, size.
This is the smoking gun for the ghost wick.
"""
import os, requests, time, json
from datetime import datetime, timezone, timedelta

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

# 14:46 ET = 18:46 UTC. Pull 14:44-14:48 ET window
start = datetime(2026, 5, 19, 18, 44, 0, tzinfo=timezone.utc)
end   = datetime(2026, 5, 19, 18, 50, 0, tzinfo=timezone.utc)
start_ns = int(start.timestamp() * 1e9)
end_ns   = int(end.timestamp() * 1e9)

print(f"Pulling 14:44-14:50 ET trades")
u = f"{BASE}/v3/trades/TSLA"
p = {"timestamp.gte": start_ns, "timestamp.lt": end_ns, "limit": 50000, "order": "asc"}

trades = []
pages = 0
while u and pages < 10:
    r = S.get(u, params=p if pages == 0 else None, timeout=60)
    j = r.json()
    trades.extend(j.get("results", []))
    u = j.get("next_url"); p = None; pages += 1

print(f"Total trades 14:44-14:50: {len(trades)}")

# Sort by price ascending and show extremes
trades.sort(key=lambda t: t.get("price", 0))
print(f"\n=== 30 LOWEST prints in this window ===")
for t in trades[:30]:
    ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
    ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(timezone(timedelta(hours=-4)))
    print(f"  ${t.get('price'):.4f}  size={t.get('size'):>5}  exch={t.get('exchange'):>2}  "
          f"conds={t.get('conditions')}  ET={ts.strftime('%H:%M:%S.%f')[:-3]}  tape={t.get('tape')}")

# Pull anything 394.5-394.8 specifically
print(f"\n=== Prints in $394.5-$394.8 range (the wick prints) ===")
wicks = [t for t in trades if 394.5 <= t.get("price", 0) <= 394.8]
for t in wicks:
    ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
    ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(timezone(timedelta(hours=-4)))
    print(f"  ${t.get('price'):.4f}  size={t.get('size')}  exch={t.get('exchange')}  "
          f"conds={t.get('conditions')}  ET={ts.strftime('%H:%M:%S.%f')[:-3]}  tape={t.get('tape')}")
    print(f"    FULL: {json.dumps(t, default=str)}")

# Condition code reference (Polygon)
print("""
=== Polygon Trade Condition Codes (relevant ones) ===
  0  = Regular sale
  2  = Average Price Trade
  12 = Form T (extended hours)
  13 = Out of Sequence
  14 = Rule 155 (NYSE)
  15 = Cross
  20 = Stopped Stock - regular trade
  21 = Yellow Flag
  36 = Cap election (NYSE)
  37 = Auto execution
  41 = Trade Through Exempt
""")
