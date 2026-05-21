"""
Verify the NEM wick today (2026-05-21) around 09:55-10:10 ET.
Show all trades in the 09:50-10:15 window, find the extreme print,
fingerprint it (lit vs TRF, conditions, size), and compare to the
surrounding price.
"""
import os, requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})
ET = timezone(timedelta(hours=-4))
TICKER = "NEM"
DAY = "2026-05-21"

def get_trades(ticker, day_str):
    d = datetime.strptime(day_str, "%Y-%m-%d")
    start = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)
    end   = datetime(d.year, d.month, d.day, 11,  0, tzinfo=ET)  # only first 1.5h
    u = f"{BASE}/v3/trades/{ticker}"
    p = {"timestamp.gte": int(start.timestamp() * 1e9),
         "timestamp.lt":  int(end.timestamp() * 1e9),
         "limit": 50000, "order": "asc"}
    trades = []
    pages = 0
    while u and pages < 400:
        r = S.get(u, params=p if pages == 0 else None, timeout=120)
        if r.status_code != 200: print(f"HTTP {r.status_code}"); break
        j = r.json()
        for t in j.get("results", []):
            ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
            if not ts_ns: continue
            ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(ET)
            trades.append({
                "t": ts, "price": t["price"], "size": t.get("size", 0),
                "exchange": t.get("exchange"), "conditions": t.get("conditions") or [],
                "trf_id": t.get("trf_id"),
            })
        u = j.get("next_url"); p = None; pages += 1
    return trades

trades = get_trades(TICKER, DAY)
print(f"Total trades 09:30-11:00: {len(trades)}")

# Build 1-min bars
by_minute = defaultdict(list)
for t in trades:
    conds = set(t["conditions"])
    if conds & {2,12,16,33,52,53}: continue
    minute = t["t"].replace(second=0, microsecond=0)
    by_minute[minute].append(t)

# Print bars 09:50-10:15
print("\n=== 1-min bars 09:50-10:15 ET ===")
print(f"{'Minute':6} {'O':>8} {'H':>8} {'L':>8} {'C':>8} {'Trades':>7} {'Vol':>8}")
for minute in sorted(by_minute):
    if minute.hour == 9 and minute.minute < 50: continue
    if minute.hour == 10 and minute.minute > 15: continue
    if minute.hour > 10: continue
    prices = [tr["price"] for tr in by_minute[minute]]
    o,h,l,c = prices[0], max(prices), min(prices), prices[-1]
    vol = sum(t['size'] for t in by_minute[minute])
    print(f"{minute.strftime('%H:%M')}  {o:>8.2f} {h:>8.2f} {l:>8.2f} {c:>8.2f} {len(prices):>7} {vol:>8}")

# Find the lowest INCLUDED trade in window 09:55-10:10
incl = [t for t in trades if not (set(t["conditions"]) & {2,12,16,33,52,53})]
window = [t for t in incl if (t["t"].hour == 9 and t["t"].minute >= 55) or
                              (t["t"].hour == 10 and t["t"].minute <= 10)]
print(f"\n=== Lowest 20 INCLUDED trades in 09:55-10:10 window ({len(window)} total) ===")
for t in sorted(window, key=lambda x: x["price"])[:20]:
    tag = ""
    if t["exchange"] == 4 and 37 in t["conditions"] and 41 in t["conditions"] and t["size"] < 100:
        tag = " [TRF-EXEMPT odd-lot]"
    elif t["exchange"] == 4:
        tag = " [TRF]"
    print(f"  {t['t'].strftime('%H:%M:%S.%f')[:-3]}  ${t['price']:>8.4f}  sz={t['size']:>5}  ex={t['exchange']:>3}  cond={t['conditions']}  trf={t.get('trf_id')}{tag}")

# Run the ghost wick detector on these bars
print("\n=== Running ghost wick detector on 9:30-11:00 ===")
bars = []
for minute in sorted(by_minute):
    prices = [tr["price"] for tr in by_minute[minute]]
    bars.append({"t": minute, "o": prices[0], "h": max(prices), "l": min(prices),
                 "c": prices[-1], "v": sum(t['size'] for t in by_minute[minute]),
                 "trades": by_minute[minute]})

for i, b in enumerate(bars):
    if i < 5 or i >= len(bars)-5: continue
    body = abs(b['o']-b['c']); upper = b['h']-max(b['o'],b['c']); lower = min(b['o'],b['c'])-b['l']
    price = (b['o']+b['c'])/2
    body_pct = body/price*100
    if body_pct >= 0.5: continue
    for direction, wick in [("up", upper), ("down", lower)]:
        if wick <= 0: continue
        wick_pct = wick/price*100
        if wick_pct < 0.5: continue
        ratio = wick/max(body, 0.0001)
        if ratio < 3: continue
        extreme = b['h'] if direction == "up" else b['l']
        body_top = max(b['o'],b['c']); body_bot = min(b['o'],b['c']); half_depth = wick/2
        isolated = True
        for j in range(i-5,i+6):
            if j == i or j<0 or j>=len(bars): continue
            nb = bars[j]
            if direction == "up" and nb['h'] >= body_top + half_depth: isolated = False; break
            if direction == "down" and nb['l'] <= body_bot - half_depth: isolated = False; break
        if not isolated: continue
        # Find extreme trade
        ext_trades = [t for t in b["trades"] if abs(t["price"]-extreme) < 0.005]
        et = ext_trades[0] if ext_trades else None
        tag = ""
        if et and et["exchange"] == 4 and 37 in et["conditions"] and 41 in et["conditions"] and et["size"] < 100:
            tag = " [TRF-EXEMPT odd-lot]"
        elif et and et["exchange"] == 4:
            tag = " [TRF]"
        print(f"  {b['t'].strftime('%H:%M')}  {direction.upper():4} ${extreme:.4f} ({wick_pct:.3f}%, ratio {ratio:.1f}, ext_size {et['size'] if et else '?'}){tag}")
