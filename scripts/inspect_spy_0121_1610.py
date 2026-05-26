"""Inspect SPY 2026-01-21 16:10 ET 5-min candle."""
import os
from datetime import datetime, timedelta, timezone
import requests

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
ET = timezone(timedelta(hours=-5))
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

r = S.get(f"{BASE}/v2/aggs/ticker/SPY/range/1/minute/2026-01-21/2026-01-21",
          params={"adjusted": "true", "sort": "asc", "limit": 50000}, timeout=60)
bars = r.json().get("results", [])
print(f"Got {len(bars)} 1-min bars for SPY 2026-01-21\n")

target = datetime(2026,1,21,16,10).replace(tzinfo=ET)
t_start_ms = int((target - timedelta(minutes=10)).timestamp() * 1000)
t_end_ms = int((target + timedelta(minutes=15)).timestamp() * 1000)
print("1-min bars 16:00 - 16:25 ET:")
for b in bars:
    if t_start_ms <= b["t"] <= t_end_ms:
        dt = datetime.fromtimestamp(b["t"]/1000, tz=ET)
        print(f"  {dt.strftime('%H:%M')} ET  O={b['o']:.3f} H={b['h']:.3f} L={b['l']:.3f} C={b['c']:.3f} V={b.get('v',0):,}")

bucket_start = int(target.timestamp()*1000)
bucket_end = bucket_start + 5*60*1000
in_b = [b for b in bars if bucket_start <= b["t"] < bucket_end]
if in_b:
    o = in_b[0]["o"]; h = max(b["h"] for b in in_b); l = min(b["l"] for b in in_b); c = in_b[-1]["c"]
    v = sum(b.get("v",0) for b in in_b)
    body_top = max(o,c); body_bot = min(o,c)
    print(f"\n5-min 16:10-16:15 ET:")
    print(f"  O={o:.3f} H={h:.3f} L={l:.3f} C={c:.3f} V={v:,}")
    print(f"  body {body_bot:.3f}-{body_top:.3f}  upper {h-body_top:.3f}  lower {body_bot-l:.3f}")
    print(f"  upper wick% = {(h-body_top)/c*100:.3f}%   lower wick% = {(body_bot-l)/c*100:.3f}%")

s_ns = int(target.timestamp()*1e9); e_ns = int((target+timedelta(minutes=5)).timestamp()*1e9)
url = f"{BASE}/v3/trades/SPY"
params = {"timestamp.gte": s_ns, "timestamp.lt": e_ns, "limit": 50000, "order": "asc"}
trades = []; pages = 0
while url and pages < 80:
    r = S.get(url, params=params if pages==0 else None, timeout=90)
    if r.status_code != 200: break
    j = r.json(); trades.extend(j.get("results", []))
    url = j.get("next_url"); params = None; pages += 1
print(f"\nTrade tape 16:10-16:15 ET: {len(trades)} trades")
if trades:
    prices = [t.get("price") for t in trades if t.get("price")]
    print(f"  price range: {min(prices):.3f} - {max(prices):.3f}")
    sorted_t = sorted(trades, key=lambda t: t.get("price") or 0)
    print(f"\n  Lowest 5 prints:")
    for t in sorted_t[:5]:
        ts = t.get("participant_timestamp")
        tstr = datetime.fromtimestamp(int(ts)/1e9, tz=ET).strftime("%H:%M:%S.%f")[:-3] if ts else "?"
        dk = t.get("exchange")==4 and t.get("trf_id") is not None
        print(f"    {tstr}  px={t.get('price')}  sz={t.get('size'):,}  dark={dk}  conds={t.get('conditions')}")
    print(f"\n  Highest 5 prints:")
    for t in sorted_t[-5:]:
        ts = t.get("participant_timestamp")
        tstr = datetime.fromtimestamp(int(ts)/1e9, tz=ET).strftime("%H:%M:%S.%f")[:-3] if ts else "?"
        dk = t.get("exchange")==4 and t.get("trf_id") is not None
        print(f"    {tstr}  px={t.get('price')}  sz={t.get('size'):,}  dark={dk}  conds={t.get('conditions')}")
