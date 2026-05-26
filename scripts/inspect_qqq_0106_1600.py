"""Inspect QQQ 2026-01-06 16:00 ET 5-min candle. Compare what Massive's
aggregator says vs the actual trade tape."""
import os
from datetime import datetime, timedelta, timezone
import requests

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
ET = timezone(timedelta(hours=-5))
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

# Get all 1-min bars for 2026-01-06
r = S.get(f"{BASE}/v2/aggs/ticker/QQQ/range/1/minute/2026-01-06/2026-01-06",
          params={"adjusted": "true", "sort": "asc", "limit": 50000}, timeout=60)
bars = r.json().get("results", [])
print(f"Got {len(bars)} 1-min bars for 2026-01-06\n")

# Look at 15:55 - 16:10 ET (window around 4PM)
target = datetime(2026,1,6,17,0).replace(tzinfo=ET)  # 16:00 CT = 17:00 ET
t_start_ms = int((target - timedelta(minutes=5)).timestamp() * 1000)
t_end_ms = int((target + timedelta(minutes=10)).timestamp() * 1000)

print(f"1-min bars around 17:00 ET (= 16:00 CT):")
for b in bars:
    if t_start_ms <= b["t"] <= t_end_ms:
        dt = datetime.fromtimestamp(b["t"]/1000, tz=ET)
        print(f"  {dt.strftime('%H:%M')} ET  O={b['o']:.3f} H={b['h']:.3f} L={b['l']:.3f} C={b['c']:.3f} V={b.get('v',0):,}")

# 5-min aggregated for the 16:00 bucket
bucket_start = int(target.timestamp()*1000)
bucket_end = bucket_start + 5*60*1000
in_bucket = [b for b in bars if bucket_start <= b["t"] < bucket_end]
if in_bucket:
    o = in_bucket[0]["o"]
    h = max(b["h"] for b in in_bucket)
    l = min(b["l"] for b in in_bucket)
    c = in_bucket[-1]["c"]
    v = sum(b.get("v",0) for b in in_bucket)
    body_top = max(o,c); body_bot = min(o,c)
    upper = h - body_top
    lower = body_bot - l
    print(f"\n5-min candle 17:00-17:05 ET / 16:00-16:05 CT (Massive aggregator):")
    print(f"  O={o:.3f} H={h:.3f} L={l:.3f} C={c:.3f} V={v:,}")
    print(f"  body: {body_bot:.3f}-{body_top:.3f}    upper wick: {upper:.3f}   lower wick: {lower:.3f}")
    print(f"  upper wick% = {upper/c*100:.3f}%    lower wick% = {lower/c*100:.3f}%")

# Now the trade tape for the 5-min window
s_ns = int(target.timestamp() * 1e9)
e_ns = int((target + timedelta(minutes=5)).timestamp() * 1e9)
url = f"{BASE}/v3/trades/QQQ"
params = {"timestamp.gte": s_ns, "timestamp.lt": e_ns, "limit": 50000, "order": "asc"}
trades = []
pages = 0
while url and pages < 50:
    r = S.get(url, params=params if pages==0 else None, timeout=90)
    if r.status_code != 200: break
    j = r.json()
    trades.extend(j.get("results", []))
    url = j.get("next_url")
    params = None
    pages += 1

print(f"\nTrade tape 17:00-17:05 ET / 16:00-16:05 CT: {len(trades)} trades")
if trades:
    prices = [t.get("price") for t in trades if t.get("price")]
    print(f"  price range: {min(prices):.3f} - {max(prices):.3f}")
    sizes = [t.get("size",0) for t in trades]
    total_sz = sum(sizes)
    total_not = sum((t.get("size") or 0) * (t.get("price") or 0) for t in trades)
    dark_sz = sum(s for t,s in zip(trades, sizes) if t.get("exchange")==4 and t.get("trf_id") is not None)
    dark_not = sum((t.get("size") or 0) * (t.get("price") or 0) for t in trades if t.get("exchange")==4 and t.get("trf_id") is not None)
    print(f"  total vol: {total_sz:,}   notional: ${total_not/1e6:.2f}M   DPR: {dark_not/total_not*100:.1f}%")
    # Show extreme prints (lowest 5 and highest 5)
    sorted_trades = sorted(trades, key=lambda t: t.get("price") or 0)
    print(f"\n  Lowest 5 prints:")
    for t in sorted_trades[:5]:
        ts = t.get("participant_timestamp")
        tstr = datetime.fromtimestamp(int(ts)/1e9, tz=ET).strftime("%H:%M:%S.%f")[:-3] if ts else "?"
        dk = t.get("exchange")==4 and t.get("trf_id") is not None
        print(f"    {tstr} ET  px={t.get('price')}  sz={t.get('size')}  dark={dk}  conds={t.get('conditions')}")
    print(f"\n  Highest 5 prints:")
    for t in sorted_trades[-5:]:
        ts = t.get("participant_timestamp")
        tstr = datetime.fromtimestamp(int(ts)/1e9, tz=ET).strftime("%H:%M:%S.%f")[:-3] if ts else "?"
        dk = t.get("exchange")==4 and t.get("trf_id") is not None
        print(f"    {tstr} ET  px={t.get('price')}  sz={t.get('size')}  dark={dk}  conds={t.get('conditions')}")
