"""Verify TSLA 4/27/2026 12:35 PM ET wick at $368.87.
Then check forward: did price come back to $368.87 within 10 trading days?"""
import os, requests, json
from datetime import datetime, timezone, timedelta

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

# 12:30 - 12:45 ET on 4/27/2026
# 4/27 ET = UTC-4. 12:30 ET = 16:30 UTC
start = datetime(2026, 4, 27, 16, 30, tzinfo=timezone.utc)
end   = datetime(2026, 4, 27, 16, 45, tzinfo=timezone.utc)
start_ns = int(start.timestamp() * 1e9)
end_ns   = int(end.timestamp() * 1e9)

# Massive's 1-min aggregate for 12:35
r = S.get(f"{BASE}/v2/aggs/ticker/TSLA/range/1/minute/2026-04-27/2026-04-27",
          params={"limit": 50000}, timeout=30)
mins = r.json().get("results", [])
print("=== TSLA 4/27 minute bars 12:30-12:45 ET (Massive) ===")
for m in mins:
    t = datetime.fromtimestamp(m['t']/1000, tz=timezone.utc).astimezone(timezone(timedelta(hours=-4)))
    if t.hour == 12 and 30 <= t.minute <= 45:
        print(f"  ET {t.strftime('%H:%M')}: O={m['o']:.2f} H={m['h']:.2f} L={m['l']:.2f} C={m['c']:.2f} V={m['v']:.0f}")
print()
print("ToS shows for 4/27 12:35 ET: O=379.41 H=379.77 L=368.87 C=378.96 R=10.9")
print()

# Pull trades 12:30-12:45
u = f"{BASE}/v3/trades/TSLA"
p = {"timestamp.gte": start_ns, "timestamp.lt": end_ns, "limit": 50000, "order": "asc"}
trades = []
pages = 0
while u and pages < 50:
    r = S.get(u, params=p if pages == 0 else None, timeout=60)
    j = r.json()
    trades.extend(j.get("results", []))
    u = j.get("next_url"); p = None; pages += 1

print(f"Trades pulled 12:30-12:45 ET: {len(trades)}")

# Prints below $370
low = [t for t in trades if t.get("price", 9999) < 370]
print(f"\n=== Prints below $370 ===")
print(f"Count: {len(low)}")
low.sort(key=lambda x: x.get("price", 0))
for t in low[:20]:
    ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
    ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(timezone(timedelta(hours=-4)))
    sip_ns = t.get("sip_timestamp")
    gap_ms = (sip_ns - t.get("participant_timestamp", sip_ns)) / 1e6 if sip_ns else 0
    print(f"  ${t.get('price'):.4f}  size={t.get('size'):>5}  exch={t.get('exchange')}  "
          f"conds={t.get('conditions')}  trf_id={t.get('trf_id', 'NONE')}  "
          f"ET={ts.strftime('%H:%M:%S.%f')[:-3]}  sip_gap={gap_ms:.0f}ms")

# Detail on the lowest print
if low:
    print(f"\n=== Full detail on the lowest print ===")
    print(json.dumps(low[0], indent=2, default=str))

# Forward test: did TSLA close at or below $368.87 within 10 trading days after 4/27?
print(f"\n=== Forward test: did price return to $368.87 after 4/27? ===")
r = S.get(f"{BASE}/v2/aggs/ticker/TSLA/range/1/day/2026-04-27/2026-05-20",
          params={"limit": 5000}, timeout=30)
days = r.json().get("results", [])
print(f"Days forward: {len(days)}")
for d in days:
    dt = datetime.fromtimestamp(d['t']/1000, tz=timezone.utc).date()
    hit_low = "<-- TOUCHED $368.87 LOW" if d['l'] <= 368.87 else ""
    print(f"  {dt}: O={d['o']:.2f} H={d['h']:.2f} L={d['l']:.2f} C={d['c']:.2f} {hit_low}")
