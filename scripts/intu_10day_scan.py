"""
INTU 10-day ghost wick scan.
Scans INTU for ghost wicks across the last 10 trading days.
Uses lower threshold (0.5%+ wick) to surface any anomalies, especially
given today's -20% catalyst move.
"""
import os, requests, time, csv, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

ET = timezone(timedelta(hours=-4))
TICKER = "INTU"
MIN_WICK_PCT = float(os.environ.get("MIN_WICK_PCT", "0.5"))

# Get last 10 trading days ending today (May 21, 2026)
def last_n_trading_days(n=10, end_date=None):
    if end_date is None:
        end_date = datetime.now(ET).date()
    days = []
    d = end_date
    while len(days) < n:
        if d.weekday() < 5:  # Mon-Fri (ignore holidays — Massive will return empty if closed)
            days.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=1)
    return list(reversed(days))

DATES = last_n_trading_days(10)
print(f"=== INTU 10-day ghost wick scan ===")
print(f"Dates: {DATES}")
print(f"Min wick %: {MIN_WICK_PCT}")
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
            print(f"  {ticker} {day_str} HTTP {r.status_code}")
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
            if wick_pct < MIN_WICK_PCT: continue
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
for day in DATES:
    print(f"\n=== {day} ===")
    bars = build_bars(TICKER, day)
    print(f"  Built {len(bars)} bars")
    if not bars:
        continue
    wicks = detect(bars)
    print(f"  Found {len(wicks)} ghost wicks")
    for w in wicks:
        w["date"] = day
        w["ticker"] = TICKER
        all_wicks.append(w)
        print(f"    {w['time_et']} {w['direction'].upper()} ${w['extreme']} ({w['wick_pct']}%, ratio {w['ratio']})")

os.makedirs("data/daily_wicks", exist_ok=True)
csv_path = f"data/daily_wicks/intu_10day.csv"
with open(csv_path, "w", newline="") as f:
    if all_wicks:
        w = csv.DictWriter(f, fieldnames=["date","ticker","time_et","direction","extreme",
                                          "open","close","body_pct","wick_pct","ratio","volume"])
        w.writeheader(); w.writerows(all_wicks)
        print(f"\nSaved {len(all_wicks)} wicks to {csv_path}")
    else:
        print(f"\nNo wicks found in {len(DATES)} days. Empty CSV.")
        f.write("date,ticker,time_et,direction,extreme,open,close,body_pct,wick_pct,ratio,volume\n")

# Summary JSON
summary = {
    "ticker": TICKER,
    "dates_scanned": DATES,
    "min_wick_pct": MIN_WICK_PCT,
    "total_wicks": len(all_wicks),
    "wicks": all_wicks
}
with open("data/daily_wicks/intu_10day.json", "w") as f:
    json.dump(summary, f, indent=2)

print(f"\n=== TOTAL: {len(all_wicks)} ghost wicks for {TICKER} across {len(DATES)} days ===")
