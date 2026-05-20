"""
Daily ghost wick scanner. Runs after market close.
Scans each configured ticker for ghost wicks IN THE SWEET SPOT (1-2% wick %, hit rate ~83%).

Detection rule (same as ghost_wick_v2.py 'trade' method):
- Build 1-min bars from /v3/trades, INCLUDING TRF prints (excluding only mechanical
  conditions 2, 12, 16, 33, 52, 53)
- body_pct < 0.5% (quiet candle)
- wick/body ratio >= 3
- 1% <= wick_pct < 2% (the sweet spot)
- Isolated: no neighbor within \u00b15 min has high/low within 50% of wick depth

Output:
- data/daily_wicks/{DATE}.csv: all ghost wicks found that day
- data/daily_wicks/latest.json: latest run summary
"""
import os, requests, time, csv, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

ET = timezone(timedelta(hours=-4))
TICKERS = os.environ.get("TICKERS", "TSLA,AMD,NVDA,PLTR,SHOP").split(",")
# Default = today (US/Eastern). Allow override via DATE env.
SCAN_DATE = os.environ.get("SCAN_DATE")
if not SCAN_DATE:
    SCAN_DATE = datetime.now(ET).strftime("%Y-%m-%d")

print(f"=== Daily ghost wick scan ===")
print(f"Tickers: {TICKERS}")
print(f"Date: {SCAN_DATE}")
print()

def build_bars(ticker, day_str):
    d = datetime.strptime(day_str, "%Y-%m-%d")
    start = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)
    end   = datetime(d.year, d.month, d.day, 16,  0, tzinfo=ET)
    u = f"{BASE}/v3/trades/{ticker}"
    p = {"timestamp.gte": int(start.timestamp() * 1e9),
         "timestamp.lt":  int(end.timestamp() * 1e9),
         "limit": 50000, "order": "asc"}
    by_minute = defaultdict(list)
    pages = 0
    while u and pages < 200:
        for attempt in range(5):
            try:
                r = S.get(u, params=p if pages == 0 else None, timeout=120); break
            except Exception:
                time.sleep(1 + attempt)
        if r.status_code != 200:
            print(f"  {ticker} HTTP {r.status_code}")
            break
        j = r.json()
        for t in j.get("results", []):
            ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
            if not ts_ns:
                continue
            conds = set(t.get("conditions") or [])
            if conds & {2, 12, 16, 33, 52, 53}:
                continue
            ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(ET)
            minute = ts.replace(second=0, microsecond=0)
            by_minute[minute].append({"price": t["price"], "size": t.get("size", 0)})
        u = j.get("next_url"); p = None; pages += 1
    bars = []
    for minute in sorted(by_minute):
        prices = [tr["price"] for tr in by_minute[minute]]
        bars.append({
            "t": minute,
            "o": prices[0], "h": max(prices), "l": min(prices), "c": prices[-1],
            "v": sum(tr["size"] for tr in by_minute[minute])
        })
    return bars

def detect(bars):
    out = []
    for i, b in enumerate(bars):
        if i < 5 or i >= len(bars) - 5:
            continue
        body = abs(b['o'] - b['c'])
        upper = b['h'] - max(b['o'], b['c'])
        lower = min(b['o'], b['c']) - b['l']
        price = (b['o'] + b['c']) / 2
        if price <= 0: continue
        body_pct = body / price * 100
        if body_pct >= 0.5: continue

        for direction, wick in [("up", upper), ("down", lower)]:
            if wick <= 0: continue
            wick_pct = wick / price * 100
            # SWEET SPOT: 1-2%
            if not (1.0 <= wick_pct < 2.0): continue
            ratio = wick / max(body, 0.0001)
            if ratio < 3: continue

            extreme = b['h'] if direction == "up" else b['l']
            body_top = max(b['o'], b['c'])
            body_bot = min(b['o'], b['c'])
            half_depth = wick / 2

            isolated = True
            for j in range(i-5, i+6):
                if j == i or j < 0 or j >= len(bars): continue
                nb = bars[j]
                if direction == "up" and nb['h'] >= body_top + half_depth:
                    isolated = False; break
                if direction == "down" and nb['l'] <= body_bot - half_depth:
                    isolated = False; break
            if not isolated: continue

            out.append({
                "time_et": b['t'].strftime("%H:%M"),
                "direction": direction,
                "extreme": round(extreme, 4),
                "open": round(b['o'], 4),
                "close": round(b['c'], 4),
                "body_pct": round(body_pct, 4),
                "wick_pct": round(wick_pct, 4),
                "ratio": round(ratio, 2),
                "volume": b['v'],
            })
    return out

all_wicks = []
for ticker in TICKERS:
    print(f"\n=== {ticker} ===")
    bars = build_bars(ticker, SCAN_DATE)
    print(f"  {len(bars)} minute bars")
    wicks = detect(bars)
    print(f"  {len(wicks)} ghost wicks in sweet spot (1-2%)")
    for w in wicks:
        w["ticker"] = ticker
        w["date"] = SCAN_DATE
        all_wicks.append(w)
        print(f"    {w['time_et']} {w['direction']} ${w['extreme']} ({w['wick_pct']}%)")

# Save
os.makedirs("data/daily_wicks", exist_ok=True)
csv_path = f"data/daily_wicks/{SCAN_DATE}.csv"
if all_wicks:
    with open(csv_path, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["date","ticker","time_et","direction","extreme",
                                            "open","close","body_pct","wick_pct","ratio","volume"])
        wr.writeheader()
        wr.writerows(all_wicks)
    print(f"\nWrote {csv_path}")

# Latest summary
with open("data/daily_wicks/latest.json", "w") as f:
    json.dump({"date": SCAN_DATE, "tickers": TICKERS, "wicks": all_wicks,
               "count": len(all_wicks)}, f, indent=2, default=str)

print(f"\n=== TOTAL: {len(all_wicks)} sweet-spot ghost wicks across {len(TICKERS)} tickers ===")
