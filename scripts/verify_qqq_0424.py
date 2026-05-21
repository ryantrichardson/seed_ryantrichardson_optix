"""
Verify what happened on QQQ 2026-04-24 around 14:04 ET.
Same diagnostic style as INTU 5/18.
"""
import os, requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

ET = timezone(timedelta(hours=-4))
TICKER = "QQQ"
DAY = "2026-04-24"

def get_trades(ticker, day_str):
    d = datetime.strptime(day_str, "%Y-%m-%d")
    start = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)
    end   = datetime(d.year, d.month, d.day, 16,  0, tzinfo=ET)
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
print(f"Total trades: {len(trades)}")

# Bars (excluding mechanical conditions)
by_minute = defaultdict(list)
for t in trades:
    conds = set(t["conditions"])
    if conds & {2, 12, 16, 33, 52, 53}: continue
    minute = t["t"].replace(second=0, microsecond=0)
    by_minute[minute].append(t)

# Bars around 13:58 - 14:10
print("\n=== Bars 13:58-14:10 (scanner view) ===")
for minute in sorted(by_minute):
    if minute.hour == 14 and 0 <= minute.minute <= 10:
        prices = [tr["price"] for tr in by_minute[minute]]
        o,h,l,c = prices[0], max(prices), min(prices), prices[-1]
        print(f"{minute.strftime('%H:%M')}  O={o:.2f}  H={h:.2f}  L={l:.2f}  C={c:.2f}  trades={len(prices)}  vol={sum(t['size'] for t in by_minute[minute])}")
    if minute.hour == 13 and minute.minute >= 58:
        prices = [tr["price"] for tr in by_minute[minute]]
        o,h,l,c = prices[0], max(prices), min(prices), prices[-1]
        print(f"{minute.strftime('%H:%M')}  O={o:.2f}  H={h:.2f}  L={l:.2f}  C={c:.2f}  trades={len(prices)}  vol={sum(t['size'] for t in by_minute[minute])}")

# Find the wick day's bar at 14:04 - what was open/close/extreme?
target_minute = None
for minute in sorted(by_minute):
    if minute.hour == 14 and minute.minute == 4:
        target_minute = minute
        break

if target_minute:
    bar_trades = by_minute[target_minute]
    prices = [t["price"] for t in bar_trades]
    o,h,l,c = prices[0], max(prices), min(prices), prices[-1]
    print(f"\n=== 14:04 bar details ===")
    print(f"O=${o:.4f}  H=${h:.4f}  L=${l:.4f}  C=${c:.4f}")
    body = abs(o-c); upper = h - max(o,c); lower = min(o,c) - l
    price = (o+c)/2
    print(f"Body: ${body:.4f} ({body/price*100:.3f}%)")
    print(f"Upper wick: ${upper:.4f} ({upper/price*100:.3f}%)")
    print(f"Lower wick: ${lower:.4f} ({lower/price*100:.3f}%)")

# All trades in 14:03 - 14:05
print("\n=== All raw trades 14:03:00 - 14:05:59 ===")
extreme_low = 1e9
extreme_low_t = None
extreme_low_trade = None
for t in trades:
    if t["t"].hour == 14 and 3 <= t["t"].minute <= 5:
        excl = "EXCLUDED" if set(t["conditions"]) & {2,12,16,33,52,53} else "INCLUDED"
        if t["price"] < extreme_low and excl == "INCLUDED":
            extreme_low = t["price"]; extreme_low_t = t["t"]; extreme_low_trade = t

# Find extreme low across day for QQQ - what was lowest INCLUDED trade
print(f"\n=== Day-wide extremes (INCLUDED trades only) ===")
incl = [t for t in trades if not (set(t["conditions"]) & {2,12,16,33,52,53})]
incl_prices = [t["price"] for t in incl]
print(f"Min: ${min(incl_prices):.4f}   Max: ${max(incl_prices):.4f}")
sorted_p = sorted(incl_prices)
n = len(sorted_p)
print(f"0.1%-99.9%: ${sorted_p[int(n*0.001)]:.2f} - ${sorted_p[int(n*0.999)]:.2f}")

# Find the lowest 5 INCLUDED trades
print(f"\n=== Lowest 10 INCLUDED trades of the day ===")
for t in sorted(incl, key=lambda x: x["price"])[:10]:
    print(f"{t['t'].strftime('%H:%M:%S.%f')[:-3]}  ${t['price']:.4f}  sz={t['size']:>5}  ex={t['exchange']:>3}  cond={t['conditions']}  trf={t.get('trf_id')}")

# And the wick extreme — search trades below the body of 14:04 bar
if target_minute:
    bar_l = min(t["price"] for t in by_minute[target_minute])
    print(f"\n=== INCLUDED trades at or near $ {bar_l:.4f} (the wick extreme) ===")
    for t in by_minute[target_minute]:
        if t["price"] <= bar_l + 0.05:
            print(f"{t['t'].strftime('%H:%M:%S.%f')[:-3]}  ${t['price']:.4f}  sz={t['size']:>5}  ex={t['exchange']:>3}  cond={t['conditions']}  trf={t.get('trf_id')}")
