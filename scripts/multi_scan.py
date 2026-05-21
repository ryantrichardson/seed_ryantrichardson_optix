"""
Multi-ticker, multi-date ghost wick scanner.
Configurable via env:
  TICKERS=RTX,TSLA,...     (comma-separated)
  DATES=2026-05-15,2026-05-18,...   (comma-separated YYYY-MM-DD)
  MIN_WICK_PCT=0.5
  OUT_TAG=rtx_5day         (filename tag)
"""
import os, requests, csv, json, time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

ET = timezone(timedelta(hours=-4))
TICKERS = os.environ["TICKERS"].split(",")
DATES = os.environ["DATES"].split(",")
MIN_WICK_PCT = float(os.environ.get("MIN_WICK_PCT", "0.5"))
OUT_TAG = os.environ.get("OUT_TAG", "scan")

print(f"=== Multi-scan ===")
print(f"Tickers: {TICKERS}")
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
    while u and pages < 400:
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
            if not ts_ns: continue
            conds = set(t.get("conditions") or [])
            if conds & {2, 12, 16, 33, 52, 53}: continue
            ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(ET)
            minute = ts.replace(second=0, microsecond=0)
            by_minute[minute].append({
                "price": t["price"], "size": t.get("size", 0),
                "exchange": t.get("exchange"), "conditions": t.get("conditions") or [],
                "ts": ts,
            })
        u = j.get("next_url"); p = None; pages += 1
    bars = []
    for minute in sorted(by_minute):
        prices = [tr["price"] for tr in by_minute[minute]]
        bars.append({
            "t": minute,
            "o": prices[0], "h": max(prices), "l": min(prices), "c": prices[-1],
            "v": sum(tr["size"] for tr in by_minute[minute]),
            "trades": by_minute[minute],
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

            # Find the extreme trade for fingerprinting
            extreme_trades = [t for t in b["trades"] if (direction == "up" and t["price"] == extreme) or (direction == "down" and t["price"] == extreme)]
            ext = extreme_trades[0] if extreme_trades else None

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
                "ext_size": ext["size"] if ext else None,
                "ext_exchange": ext["exchange"] if ext else None,
                "ext_conds": ext["conditions"] if ext else None,
                "ext_ts": ext["ts"].strftime("%H:%M:%S.%f")[:-3] if ext else None,
            })
    return out

all_wicks = []
for ticker in TICKERS:
    for day in DATES:
        print(f"\n=== {ticker} {day} ===")
        bars = build_bars(ticker, day)
        print(f"  Built {len(bars)} bars")
        if not bars: continue
        wicks = detect(bars)
        print(f"  Found {len(wicks)} ghost wicks")
        for w in wicks:
            w["date"] = day; w["ticker"] = ticker
            all_wicks.append(w)
            tag = ""
            if w["ext_conds"] and 37 in w["ext_conds"] and 41 in w["ext_conds"]:
                tag = " [TRF-EXEMPT odd-lot]"
            print(f"    {w['time_et']} {w['direction'].upper():4} ${w['extreme']} ({w['wick_pct']}%, ratio {w['ratio']}, ext_size {w['ext_size']}){tag}")

os.makedirs("data/daily_wicks", exist_ok=True)
csv_path = f"data/daily_wicks/{OUT_TAG}.csv"
with open(csv_path, "w", newline="") as f:
    if all_wicks:
        w = csv.DictWriter(f, fieldnames=["date","ticker","time_et","direction","extreme",
                                          "open","close","body_pct","wick_pct","ratio","volume",
                                          "ext_size","ext_exchange","ext_conds","ext_ts"])
        w.writeheader(); w.writerows(all_wicks)
        print(f"\nSaved {len(all_wicks)} wicks to {csv_path}")
    else:
        f.write("date,ticker,time_et,direction,extreme,open,close,body_pct,wick_pct,ratio,volume,ext_size,ext_exchange,ext_conds,ext_ts\n")
        print(f"\nNo wicks found. Empty CSV.")

with open(f"data/daily_wicks/{OUT_TAG}.json", "w") as f:
    json.dump({"tickers": TICKERS, "dates": DATES, "min_wick_pct": MIN_WICK_PCT,
               "total": len(all_wicks), "wicks": all_wicks}, f, indent=2, default=str)

print(f"\n=== TOTAL: {len(all_wicks)} ghost wicks ===")
