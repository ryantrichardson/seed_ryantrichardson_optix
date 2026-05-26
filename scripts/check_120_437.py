"""
Verify the claim that TSLA hit 437.50 at 10:17 AM ET on 1/20/26.
Look at:
  - Massive 1-min bars for 1/20/26 from 10:00 to 11:00 ET
  - All trades 10:15-10:20 ET specifically, with full timestamp/price/cond info
"""
import os, json
from datetime import datetime, timedelta, timezone
import requests

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
ET = timezone(timedelta(hours=-5))  # EST in January
S = requests.Session(); S.headers.update({"Authorization": f"Bearer {API}"})

# Day-range minute bars
print("=== TSLA 1/20/26 minute bars 10:00-11:00 ET ===")
r = S.get(f"{BASE}/v2/aggs/ticker/TSLA/range/1/minute/2026-01-20/2026-01-20",
          params={"adjusted":"true", "sort":"asc", "limit":50000}, timeout=60)
bars = r.json().get("results", [])
print(f"{len(bars)} 1-min bars for the day")
for b in bars:
    dt = datetime.fromtimestamp(b["t"]/1000, tz=ET)
    if 10 <= dt.hour < 11:
        print(f"  {dt.strftime('%H:%M ET')}  O={b['o']:.2f} H={b['h']:.2f} L={b['l']:.2f} C={b['c']:.2f}  V={b.get('v',0):,}")

# Trades 10:15-10:20 with full detail
print("\n=== Trades 10:15-10:20 ET on 1/20/26 ===")
s_dt = datetime(2026,1,20,10,15,tzinfo=ET)
e_dt = datetime(2026,1,20,10,20,tzinfo=ET)
s_ns = int(s_dt.timestamp()*1e9)
e_ns = int(e_dt.timestamp()*1e9)
r = S.get(f"{BASE}/v3/trades/TSLA",
          params={"timestamp.gte":s_ns,"timestamp.lt":e_ns,"limit":50000,"order":"asc"}, timeout=90)
trades = r.json().get("results", [])
print(f"{len(trades)} trades")

# Find any trade at >= 437
big = [t for t in trades if (t.get("price") or 0) >= 437]
print(f"\nTrades with price >= 437.00:  {len(big)}")
for t in big[:20]:
    ps = t.get("participant_timestamp")
    ss = t.get("sip_timestamp")
    p_et = datetime.fromtimestamp(int(ps)/1e9, tz=ET).strftime("%H:%M:%S.%f")[:-3] if ps else "?"
    s_et = datetime.fromtimestamp(int(ss)/1e9, tz=ET).strftime("%H:%M:%S.%f")[:-3] if ss else "?"
    print(f"  px={t.get('price')}  sz={t.get('size')}  participant={p_et}  sip={s_et}  exch={t.get('exchange')}  trf={t.get('trf_id')}  conds={t.get('conditions')}")

# Also look at the prices around 10:17 ET in general
print("\n=== Top 20 highest prices in 10:15-10:20 window ===")
sorted_high = sorted(trades, key=lambda t: -(t.get("price") or 0))[:20]
for t in sorted_high:
    ps = t.get("participant_timestamp")
    p_et = datetime.fromtimestamp(int(ps)/1e9, tz=ET).strftime("%H:%M:%S.%f")[:-3] if ps else "?"
    print(f"  px={t.get('price')}  sz={t.get('size')}  participant={p_et}  exch={t.get('exchange')}  trf={t.get('trf_id')}  conds={t.get('conditions')}")
