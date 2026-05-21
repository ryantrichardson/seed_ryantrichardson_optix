"""
Re-probe Massive options endpoints more carefully.
Try multiple option formats and endpoint shapes.
"""
import os, requests
from datetime import datetime, timezone, timedelta

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

# Step 1: get a real, current MSFT option contract from the contracts endpoint
print("=== Step 1: Find a real active MSFT option contract ===")
r = S.get(f"{BASE}/v3/reference/options/contracts",
         params={"underlying_ticker":"MSFT","limit":5,"expired":"false","order":"asc","sort":"expiration_date"},
         timeout=20)
print(f"Status: {r.status_code}")
contracts = r.json().get("results", [])
for c in contracts[:5]:
    print(f"  {c.get('ticker'):30}  strike={c.get('strike_price')}  exp={c.get('expiration_date')}  type={c.get('contract_type')}")

if not contracts:
    print("No contracts returned — bailing")
    raise SystemExit

real_ticker = contracts[0]["ticker"]
print(f"\nUsing real option ticker: {real_ticker}")

# Step 2: probe options-specific endpoints with the real ticker
print("\n=== Step 2: Probe options endpoints with real ticker ===")
options_endpoints = [
    ("Options trades (v3)",     f"/v3/trades/{real_ticker}", {"limit":3}),
    ("Options quotes (v3)",     f"/v3/quotes/{real_ticker}", {"limit":3}),
    ("Last option trade",       f"/v2/last/trade/{real_ticker}", {}),
    ("Last option quote",       f"/v2/last/nbbo/{real_ticker}", {}),
    ("Option daily aggs",       f"/v2/aggs/ticker/{real_ticker}/range/1/day/2026-04-21/2026-05-21", {}),
    ("Option minute aggs",      f"/v2/aggs/ticker/{real_ticker}/range/1/minute/2026-05-21/2026-05-21", {"limit":3}),
    ("Option snapshot single",  f"/v3/snapshot/options/MSFT/{real_ticker}", {}),
    ("Option chain (v3)",       f"/v3/snapshot/options/MSFT", {"limit":3}),
    ("Option chain greeks",     f"/v3/snapshot/options/MSFT", {"limit":3,"contract_type":"call"}),
    ("Option contract detail",  f"/v3/reference/options/contracts/{real_ticker}", {}),
    ("Open interest",           f"/v3/snapshot/options/MSFT", {"limit":3}),  # OI is in snapshot
    # Universe-level
    ("Unified snapshot",        f"/v3/snapshot", {"ticker.any_of":real_ticker}),
]

for name, path, params in options_endpoints:
    url = BASE + path
    try:
        r = S.get(url, params=params, timeout=20)
        status = r.status_code
        if status == 200:
            data = r.json()
            n = 0
            if isinstance(data, dict):
                n = len(data.get("results", []) or [])
            keys = list(data.keys())[:6] if isinstance(data, dict) else "list"
            print(f"  ✓ {name:28} {status}  results={n:>4}  keys={keys}")
            # Show a sample of the first result for snapshots
            if n > 0 and "snapshot" in path.lower():
                first = data["results"][0] if isinstance(data["results"], list) else data["results"]
                if isinstance(first, dict):
                    print(f"      sample fields: {list(first.keys())[:15]}")
        else:
            try: err = (r.json().get("error") or r.json().get("message") or r.text[:120])
            except: err = r.text[:120]
            print(f"  ✗ {name:28} {status}  {err}")
    except Exception as e:
        print(f"  ! {name:28} ERROR {str(e)[:80]}")

# Step 3: pull MSFT options snapshot and show what's in it (greeks, IV, OI, etc.)
print("\n=== Step 3: MSFT options snapshot sample (greeks/IV/OI/depth) ===")
r = S.get(f"{BASE}/v3/snapshot/options/MSFT", params={"limit":3}, timeout=20)
if r.status_code == 200:
    data = r.json().get("results", [])
    for opt in data[:3]:
        print(f"\n  {opt.get('details',{}).get('ticker', '?')}:")
        for key in ["details","greeks","implied_volatility","open_interest","day","last_trade","last_quote","underlying_asset"]:
            if key in opt:
                v = opt[key]
                if isinstance(v, dict):
                    print(f"    {key}: {list(v.keys())}")
                else:
                    print(f"    {key}: {v}")
