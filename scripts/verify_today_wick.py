"""Verify the TSLA 5/20 12:35 ET wick at $368.87."""
import os, requests, json
from datetime import datetime, timezone, timedelta

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

# 12:30-12:40 ET on 5/20/2026
# ET = UTC-4. 12:30 ET = 16:30 UTC
start = datetime(2026, 5, 20, 16, 30, tzinfo=timezone.utc)
end   = datetime(2026, 5, 20, 16, 45, tzinfo=timezone.utc)
start_ns = int(start.timestamp() * 1e9)
end_ns   = int(end.timestamp() * 1e9)

# 1-min aggregate for 12:35 specifically
r = S.get(f"{BASE}/v2/aggs/ticker/TSLA/range/1/minute/2026-05-20/2026-05-20",
          params={"limit": 50000}, timeout=30)
mins = r.json().get("results", [])

print("=== TSLA 5/20 minute bars 12:30-12:45 ET (Massive) ===")
for m in mins:
    t = datetime.fromtimestamp(m['t']/1000, tz=timezone.utc).astimezone(timezone(timedelta(hours=-4)))
    if t.hour == 12 and 30 <= t.minute <= 45:
        print(f"  ET {t.strftime('%H:%M')}: O={m['o']:.2f} H={m['h']:.2f} L={m['l']:.2f} C={m['c']:.2f} V={m['v']:.0f}")
print()
print("ToS shows for 12:35 ET: O=379.41 H=379.77 L=368.87 C=378.96 R=10.9")
print()

# Now pull all trades in this window
u = f"{BASE}/v3/trades/TSLA"
p = {"timestamp.gte": start_ns, "timestamp.lt": end_ns, "limit": 50000, "order": "asc"}
trades = []
pages = 0
while u and pages < 50:
    r = S.get(u, params=p if pages == 0 else None, timeout=60)
    j = r.json()
    trades.extend(j.get("results", []))
    u = j.get("next_url"); p = None; pages += 1

print(f"Total trades 12:30-12:45 ET: {len(trades)}")

# Find any prints below $370
print(f"\n=== Prints below $370 in this window ===")
low = [t for t in trades if t.get("price", 9999) < 370]
print(f"Count: {len(low)}")
low.sort(key=lambda x: x.get("price", 0))
for t in low[:30]:
    ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
    ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(timezone(timedelta(hours=-4)))
    sip_ns = t.get("sip_timestamp")
    gap_ms = (sip_ns - t.get("participant_timestamp", sip_ns)) / 1e6 if sip_ns else 0
    print(f"  ${t.get('price'):.4f}  size={t.get('size'):>5}  exch={t.get('exchange')}  "
          f"conds={t.get('conditions')}  trf_id={t.get('trf_id', 'NONE')}  "
          f"ET={ts.strftime('%H:%M:%S.%f')[:-3]}  sip_gap={gap_ms:.0f}ms")

# Also check 12:35 specifically
print(f"\n=== Lowest 10 prints overall in 12:30-12:45 window ===")
trades.sort(key=lambda x: x.get("price", 0))
for t in trades[:10]:
    ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
    ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(timezone(timedelta(hours=-4)))
    print(f"  ${t.get('price'):.4f}  size={t.get('size'):>5}  exch={t.get('exchange')}  "
          f"conds={t.get('conditions')}  trf_id={t.get('trf_id', 'NONE')}  "
          f"ET={ts.strftime('%H:%M:%S.%f')[:-3]}")

# What is the actual lit price at 12:35?
print(f"\n=== 12:35 lit price (median of all prints that minute) ===")
m1235 = [t for t in trades if t.get("participant_timestamp") and
         datetime.fromtimestamp(t["participant_timestamp"]/1e9, tz=timezone.utc).astimezone(
             timezone(timedelta(hours=-4))).strftime("%H:%M") == "12:35"]
m1235_px = sorted([t["price"] for t in m1235])
print(f"  12:35 trade count: {len(m1235)}")
if m1235_px:
    print(f"  min={m1235_px[0]:.2f}  max={m1235_px[-1]:.2f}  median={m1235_px[len(m1235_px)//2]:.2f}")

# Show conditions of any sub-370 print in detail
if low:
    print(f"\n=== Full detail on the wick prints (sub-$370) ===")
    for t in low[:5]:
        print(json.dumps(t, indent=2, default=str))
