"""
Verify what happened on INTU 2026-05-18 around 15:31 — was the $440.14 high
a real trade or an isolated outlier print?

Pulls the surrounding 1-min bars and lists the actual trades in the 15:30-15:33 window.
"""
import os, requests, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

ET = timezone(timedelta(hours=-4))
TICKER = "INTU"
DAY = "2026-05-18"

# Full intraday bars (same logic as scanner)
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
    while u and pages < 200:
        r = S.get(u, params=p if pages == 0 else None, timeout=120)
        if r.status_code != 200:
            print(f"HTTP {r.status_code}"); break
        j = r.json()
        for t in j.get("results", []):
            ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
            if not ts_ns: continue
            ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(ET)
            trades.append({
                "t": ts,
                "price": t["price"],
                "size": t.get("size", 0),
                "exchange": t.get("exchange"),
                "conditions": t.get("conditions") or [],
                "trf_id": t.get("trf_id"),
                "trf_timestamp": t.get("trf_timestamp"),
            })
        u = j.get("next_url"); p = None; pages += 1
    return trades

trades = get_trades(TICKER, DAY)
print(f"Total trades: {len(trades)}")

# Bars (excluding mechanical conditions, same as scanner)
by_minute = defaultdict(list)
for t in trades:
    conds = set(t["conditions"])
    if conds & {2, 12, 16, 33, 52, 53}:
        continue
    minute = t["t"].replace(second=0, microsecond=0)
    by_minute[minute].append(t)

# Print bars around 15:25 - 15:36
print("\n=== Bars 15:25-15:36 (INCLUDED trades, scanner view) ===")
for minute in sorted(by_minute):
    if minute.hour == 15 and 25 <= minute.minute <= 36:
        prices = [tr["price"] for tr in by_minute[minute]]
        o,h,l,c = prices[0], max(prices), min(prices), prices[-1]
        print(f"{minute.strftime('%H:%M')}  O={o:.2f}  H={h:.2f}  L={l:.2f}  C={c:.2f}  trades={len(prices)}  vol={sum(t['size'] for t in by_minute[minute])}")

# Now print INDIVIDUAL trades in the wick window
print("\n=== All raw trades 15:30:00 - 15:32:59 (incl. excluded conditions) ===")
for t in trades:
    if t["t"].hour == 15 and 30 <= t["t"].minute <= 32:
        excl = "EXCLUDED" if set(t["conditions"]) & {2, 12, 16, 33, 52, 53} else "INCLUDED"
        print(f"{t['t'].strftime('%H:%M:%S.%f')[:-3]}  ${t['price']:.4f}  sz={t['size']:>5}  ex={t['exchange']:>3}  cond={t['conditions']}  trf={t.get('trf_id')}  {excl}")

# Specifically find any prints >= 430
print("\n=== Any trades >= $430 in the entire day ===")
for t in trades:
    if t["price"] >= 430:
        excl = "EXCLUDED" if set(t["conditions"]) & {2, 12, 16, 33, 52, 53} else "INCLUDED"
        print(f"{t['t'].strftime('%H:%M:%S.%f')[:-3]}  ${t['price']:.4f}  sz={t['size']:>5}  ex={t['exchange']:>3}  cond={t['conditions']}  trf={t.get('trf_id')}  {excl}")

# And what was the contextual price range?
all_prices = [t["price"] for t in trades]
print(f"\n=== Day stats ===")
print(f"Min: ${min(all_prices):.2f}   Max: ${max(all_prices):.2f}")
# Exclude top/bottom 0.1% to find "real" range
import statistics
sorted_p = sorted(all_prices)
n = len(sorted_p)
p01 = sorted_p[int(n*0.001)]
p999 = sorted_p[int(n*0.999)]
print(f"0.1%-99.9%: ${p01:.2f} - ${p999:.2f}")
