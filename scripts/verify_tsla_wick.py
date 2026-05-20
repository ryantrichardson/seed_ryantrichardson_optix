"""
Verify the TSLA 13:46 ET wick on 5/19/2026.
Pull every /v3/trades print between 13:45:00 and 13:47:00 ET,
find the 394.xx print, tag its exchange + conditions.

Polygon/Massive trade conditions:
  - Exchange ID 4 = NYSE TRF, ID 36 = TRF (alt), ID 37 = ADF
  - Condition codes: 12=Form T, 13=Out of Sequence, etc.
  - TRF / Form T = off-exchange / dark pool / ATS print
"""
import os, requests, time, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})
TICKER = "TSLA"

# Window: 13:44 - 13:48 ET on 2026-05-19
# ET = UTC-4 (DST), so 13:44 ET = 17:44 UTC, 13:48 ET = 17:48 UTC
start = datetime(2026, 5, 19, 17, 44, 0, tzinfo=timezone.utc)
end   = datetime(2026, 5, 19, 17, 48, 0, tzinfo=timezone.utc)
start_ns = int(start.timestamp() * 1e9)
end_ns   = int(end.timestamp() * 1e9)

print(f"Pulling TSLA trades {start.isoformat()} to {end.isoformat()} UTC")
print(f"(= 13:44 to 13:48 ET on 5/19/2026)")
print()

u = f"{BASE}/v3/trades/{TICKER}"
p = {"timestamp.gte": start_ns, "timestamp.lt": end_ns, "limit": 50000, "order": "asc"}

all_trades = []
pages = 0
while u and pages < 50:
    for attempt in range(5):
        try:
            r = S.get(u, params=p if pages == 0 else None, timeout=60)
            break
        except Exception as e:
            print(f"  retry {attempt}: {e}")
            time.sleep(1 + attempt)
    if r.status_code != 200:
        print(f"HTTP {r.status_code}: {r.text[:300]}")
        break
    j = r.json()
    results = j.get("results", [])
    all_trades.extend(results)
    print(f"  page {pages}: {len(results)} trades")
    u = j.get("next_url")
    p = None
    pages += 1

print(f"\nTotal trades pulled: {len(all_trades)}")
if not all_trades:
    print("NO DATA")
    exit()

# Show sample structure of one trade
print("\n=== Sample trade structure ===")
print(json.dumps(all_trades[0], indent=2, default=str))

# Find the wick: any print below 400 (the candle low was 394.635)
low_prints = [t for t in all_trades if t.get("price", 9999) < 400]
print(f"\n=== Prints below $400 (the wick) ===")
print(f"Found {len(low_prints)} prints below $400")

for t in sorted(low_prints, key=lambda x: x.get("price", 0))[:20]:
    ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp") or t.get("trf_timestamp")
    ts = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc) if ts_ns else None
    et = ts.astimezone(timezone(timedelta(hours=-4))) if ts else None
    print(f"  price=${t.get('price'):.3f}  size={t.get('size')}  exch={t.get('exchange')}  "
          f"conds={t.get('conditions')}  trf_id={t.get('trf_id', 'N/A')}  "
          f"trf_ts={'YES' if t.get('trf_timestamp') else 'NO'}  "
          f"ET={et.strftime('%H:%M:%S.%f')[:-3] if et else 'N/A'}")

# Bucket by exchange across the whole window
print(f"\n=== All {len(all_trades)} trades 13:44-13:48 ET by exchange ===")
by_exch = defaultdict(lambda: {"count": 0, "size": 0, "min_px": 1e9, "max_px": 0})
for t in all_trades:
    e = t.get("exchange", "?")
    by_exch[e]["count"] += 1
    by_exch[e]["size"] += t.get("size", 0)
    by_exch[e]["min_px"] = min(by_exch[e]["min_px"], t.get("price", 1e9))
    by_exch[e]["max_px"] = max(by_exch[e]["max_px"], t.get("price", 0))

for e, d in sorted(by_exch.items()):
    print(f"  exch={e:>3}  count={d['count']:>6}  size={d['size']:>10}  range=${d['min_px']:.3f}-${d['max_px']:.3f}")

# Polygon exchange code map (the important ones)
print()
print("Exchange code reference:")
print("  4 = NYSE")
print("  12 = Nasdaq")
print("  19 = FINRA ADF (off-exchange)")
print("  36 = FINRA TRF Nasdaq (DARK POOL / ATS)")
print("  37 = FINRA TRF NYSE (DARK POOL / ATS)")

# Bucket the low prints (below 400) by exchange specifically
print(f"\n=== Wick prints (below $400) by exchange ===")
wick_exch = defaultdict(lambda: {"count": 0, "size": 0, "min_px": 1e9})
for t in low_prints:
    e = t.get("exchange", "?")
    wick_exch[e]["count"] += 1
    wick_exch[e]["size"] += t.get("size", 0)
    wick_exch[e]["min_px"] = min(wick_exch[e]["min_px"], t.get("price", 1e9))

for e, d in sorted(wick_exch.items()):
    print(f"  exch={e:>3}  count={d['count']:>4}  size={d['size']:>8}  lowest=${d['min_px']:.3f}")
