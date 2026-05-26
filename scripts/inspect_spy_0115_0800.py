"""Inspect SPY 2026-01-15 08:00 ET 5-min candle."""
import os
from datetime import datetime, timedelta, timezone
import requests

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
ET = timezone(timedelta(hours=-5))
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

r = S.get(f"{BASE}/v2/aggs/ticker/SPY/range/1/minute/2026-01-15/2026-01-15",
          params={"adjusted": "true", "sort": "asc", "limit": 50000}, timeout=60)
bars = r.json().get("results", [])
print(f"Got {len(bars)} 1-min bars for SPY 2026-01-15\n")

target = datetime(2026,1,15,8,0).replace(tzinfo=ET)
t_start_ms = int((target - timedelta(minutes=5)).timestamp() * 1000)
t_end_ms = int((target + timedelta(minutes=10)).timestamp() * 1000)
print("1-min bars 07:55 - 08:10 ET:")
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
    print(f"\n5-min 08:00-08:05 ET (Massive aggregator):")
    print(f"  O={o:.3f} H={h:.3f} L={l:.3f} C={c:.3f} V={v:,}")
    print(f"  body {body_bot:.3f}-{body_top:.3f}  upper {h-body_top:.3f}  lower {body_bot-l:.3f}")
    print(f"  lower wick% = {(body_bot-l)/c*100:.3f}%")

# Trade tape
s_ns = int(target.timestamp()*1e9); e_ns = int((target+timedelta(minutes=5)).timestamp()*1e9)
url = f"{BASE}/v3/trades/SPY"
params = {"timestamp.gte": s_ns, "timestamp.lt": e_ns, "limit": 50000, "order": "asc"}
trades = []; pages = 0
while url and pages < 50:
    r = S.get(url, params=params if pages==0 else None, timeout=90)
    if r.status_code != 200: break
    j = r.json(); trades.extend(j.get("results", []))
    url = j.get("next_url"); params = None; pages += 1
print(f"\nTrade tape 08:00-08:05 ET: {len(trades)} trades")
if trades:
    prices = [t.get("price") for t in trades if t.get("price")]
    print(f"  price range: {min(prices):.3f} - {max(prices):.3f}")
    # Show 10 lowest
    sorted_t = sorted(trades, key=lambda t: t.get("price") or 0)
    print(f"\n  Lowest 10 prints:")
    for t in sorted_t[:10]:
        ts = t.get("participant_timestamp")
        tstr = datetime.fromtimestamp(int(ts)/1e9, tz=ET).strftime("%H:%M:%S.%f")[:-3] if ts else "?"
        dk = t.get("exchange")==4 and t.get("trf_id") is not None
        print(f"    {tstr}  px={t.get('price')}  sz={t.get('size'):,}  dark={dk}  conds={t.get('conditions')}")

    # ToS-style filtered: drop conditions 7,14,22,37 and sizes <100
    HIDDEN = {7, 14, 22, 37}
    visible = [t for t in trades if not (set(t.get("conditions") or []) & HIDDEN) and (t.get("size") or 0) >= 100]
    print(f"\n  ToS-style visible trades (no cond 7/14/22/37, size>=100): {len(visible)}")
    if visible:
        vp = [t.get("price") for t in visible]
        print(f"    visible price range: {min(vp):.3f} - {max(vp):.3f}")
        sv = sorted(visible, key=lambda t: t.get("price") or 0)
        print(f"    Lowest 5 visible:")
        for t in sv[:5]:
            ts = t.get("participant_timestamp")
            tstr = datetime.fromtimestamp(int(ts)/1e9, tz=ET).strftime("%H:%M:%S.%f")[:-3] if ts else "?"
            dk = t.get("exchange")==4 and t.get("trf_id") is not None
            print(f"      {tstr}  px={t.get('price')}  sz={t.get('size'):,}  dark={dk}  conds={t.get('conditions')}")
