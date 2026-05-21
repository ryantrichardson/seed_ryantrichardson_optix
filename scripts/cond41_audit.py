"""
Two-part audit:
A) How common is condition 41 across regular MSFT trading? Is it rare/special or common/mundane?
B) What endpoints does our Massive API key have access to? (capability probe)
"""
import os, requests, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict, Counter

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})
ET = timezone(timedelta(hours=-4))

# ============ PART A: Condition 41 prevalence ============
print("="*70)
print("PART A: How common is condition 41?")
print("="*70)

# Pull 30 minutes of MSFT trades on a normal day to count cond distribution
DAY = "2026-05-18"
d = datetime.strptime(DAY, "%Y-%m-%d")
start = datetime(d.year, d.month, d.day, 10, 0, tzinfo=ET)
end   = datetime(d.year, d.month, d.day, 10, 30, tzinfo=ET)
u = f"{BASE}/v3/trades/MSFT"
p = {"timestamp.gte": int(start.timestamp() * 1e9),
     "timestamp.lt":  int(end.timestamp() * 1e9),
     "limit": 50000, "order": "asc"}
trades = []
pages = 0
while u and pages < 200:
    r = S.get(u, params=p if pages == 0 else None, timeout=120)
    if r.status_code != 200: break
    j = r.json()
    for t in j.get("results", []):
        trades.append(t)
    u = j.get("next_url"); p = None; pages += 1

print(f"\nSample: MSFT trades 10:00-10:30 ET on 2026-05-18 (n={len(trades)})")

cond_counter = Counter()
cond41_total = 0
cond41_by_exchange = Counter()
cond41_outside_nbbo_proxy = 0  # we'll check vs first/last price as proxy
prices = [t["price"] for t in trades]
median_price = sorted(prices)[len(prices)//2] if prices else 0

for t in trades:
    conds = t.get("conditions") or []
    for c in conds:
        cond_counter[c] += 1
    if 41 in conds:
        cond41_total += 1
        cond41_by_exchange[t.get("exchange")] += 1
        # how far from median?
        if abs(t["price"] - median_price) > 0.5:
            cond41_outside_nbbo_proxy += 1

print(f"\nCondition code frequencies (top 20):")
for c, n in cond_counter.most_common(20):
    pct = n/len(trades)*100
    print(f"  cond {c:>3}: {n:>6} ({pct:5.2f}% of trades)")

print(f"\nCondition 41 specifically:")
print(f"  Total cond 41 prints: {cond41_total} ({cond41_total/len(trades)*100:.2f}% of all trades)")
print(f"  By exchange: {dict(cond41_by_exchange)}")
print(f"  Prints > $0.50 from median price: {cond41_outside_nbbo_proxy} ({cond41_outside_nbbo_proxy/max(cond41_total,1)*100:.2f}% of cond 41)")

# ============ PART B: Endpoint capability probe ============
print("\n" + "="*70)
print("PART B: Massive API endpoint access probe")
print("="*70)

# Test endpoints we know exist or might exist on Polygon-style APIs
endpoints_to_probe = [
    # Reference data
    ("Tickers ref",        "/v3/reference/tickers", {"limit": 1}),
    ("Ticker details",     "/v3/reference/tickers/MSFT", {}),
    ("Ticker news",        "/v2/reference/news", {"ticker":"MSFT","limit":1}),
    ("Conditions ref",     "/v3/reference/conditions", {"asset_class":"stocks","limit":5}),
    ("Exchanges ref",      "/v3/reference/exchanges", {"asset_class":"stocks"}),
    ("Splits",             "/v3/reference/splits", {"ticker":"MSFT","limit":1}),
    ("Dividends",          "/v3/reference/dividends", {"ticker":"MSFT","limit":1}),
    # Market data - aggregates
    ("Daily aggs",         "/v2/aggs/ticker/MSFT/range/1/day/2026-05-18/2026-05-18", {}),
    ("Minute aggs",        "/v2/aggs/ticker/MSFT/range/1/minute/2026-05-18/2026-05-18", {"limit":1}),
    ("Grouped daily",      "/v2/aggs/grouped/locale/us/market/stocks/2026-05-18", {"limit":1}),
    ("Daily open/close",   "/v1/open-close/MSFT/2026-05-18", {}),
    ("Previous close",     "/v2/aggs/ticker/MSFT/prev", {}),
    # Trades & quotes
    ("Trades v3",          "/v3/trades/MSFT", {"limit":1}),
    ("Quotes (NBBO)",      "/v3/quotes/MSFT", {"limit":1,"timestamp.gte":int(start.timestamp()*1e9)}),
    ("Last trade",         "/v2/last/trade/MSFT", {}),
    ("Last quote",         "/v2/last/nbbo/MSFT", {}),
    # Snapshots
    ("Snapshot all",       "/v2/snapshot/locale/us/markets/stocks/tickers", {"tickers":"MSFT"}),
    ("Snapshot single",    "/v2/snapshot/locale/us/markets/stocks/tickers/MSFT", {}),
    ("Snapshot gainers",   "/v2/snapshot/locale/us/markets/stocks/gainers", {}),
    # Options
    ("Options contracts",  "/v3/reference/options/contracts", {"underlying_ticker":"MSFT","limit":1}),
    ("Options trades",     "/v3/trades/O:MSFT260619C00420000", {"limit":1}),
    ("Options snapshot",   "/v3/snapshot/options/MSFT", {"limit":1}),
    # Technical indicators (Polygon-style)
    ("SMA",                "/v1/indicators/sma/MSFT", {"timespan":"day","limit":1}),
    ("RSI",                "/v1/indicators/rsi/MSFT", {"timespan":"day","limit":1}),
    # Forex/Crypto
    ("Crypto last",        "/v1/last/crypto/BTC/USD", {}),
    # Short interest / dark pool / SI
    ("Short interest",     "/stocks/v1/short-interest", {"ticker":"MSFT","limit":1}),
    ("Short volume",       "/stocks/v1/short-volume", {"ticker":"MSFT","limit":1}),
    ("FTD (failures)",     "/stocks/v1/ftd", {"ticker":"MSFT","limit":1}),
    # Financials
    ("Financials",         "/vX/reference/financials", {"ticker":"MSFT","limit":1}),
    # Treasury / FRED-like
    ("Treasury yields",    "/fed/v1/treasury-yields", {"limit":1}),
    ("Inflation",          "/fed/v1/inflation", {"limit":1}),
    # Forex
    ("FX last",            "/v1/last_quote/currencies/EUR/USD", {}),
    ("FX agg",             "/v2/aggs/ticker/C:EURUSD/range/1/day/2026-05-18/2026-05-18", {}),
    # Indices
    ("Index agg",          "/v2/aggs/ticker/I:SPX/range/1/day/2026-05-18/2026-05-18", {}),
    ("Index snapshot",     "/v3/snapshot/indices", {"ticker.any_of":"I:SPX"}),
    # Universe / market status
    ("Market status",      "/v1/marketstatus/now", {}),
    ("Market holidays",    "/v1/marketstatus/upcoming", {}),
]

results = []
for name, path, params in endpoints_to_probe:
    url = BASE + path
    try:
        r = S.get(url, params=params, timeout=15)
        status = r.status_code
        if status == 200:
            data = r.json()
            n = 0
            if isinstance(data, dict):
                n = len(data.get("results", []) or data.get("ticker", []) or [])
            sample_keys = list(data.keys())[:5] if isinstance(data, dict) else "list"
            results.append((name, status, n, sample_keys))
            print(f"  ✓ {name:25} {status}  results={n:>4}  keys={sample_keys}")
        elif status in (401, 403):
            results.append((name, status, 0, "NOT_ENTITLED"))
            print(f"  ✗ {name:25} {status}  NOT ENTITLED")
        elif status == 404:
            results.append((name, status, 0, "NOT_FOUND"))
            print(f"  ? {name:25} {status}  (endpoint does not exist)")
        else:
            try:
                err = r.json().get("error") or r.json().get("message") or r.text[:100]
            except: err = r.text[:100]
            results.append((name, status, 0, err))
            print(f"  ! {name:25} {status}  {err}")
    except Exception as e:
        print(f"  ! {name:25} ERROR {e}")

print(f"\n=== Summary: {sum(1 for _,s,_,_ in results if s==200)} accessible / {len(results)} probed ===")
