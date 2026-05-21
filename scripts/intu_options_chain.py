"""
Pull INTU options chain from Massive for ~1 year LEAPs.
Filters: call only, strike between $200 and $550, expiration between 2027-04-01 and 2027-07-31.
Output: data/options/INTU_leaps_<date>.json + a markdown table to stdout.
"""
import os, requests, json, csv, sys
from datetime import datetime, timezone

API = os.environ["MASSIVE_API_KEY"]
TICKER = "INTU"
OUT_DIR = "data/options"
os.makedirs(OUT_DIR, exist_ok=True)

base = f"https://api.massive.com/v3/snapshot/options/{TICKER}"

# Pull all calls expiring between Apr 2027 and Jul 2027 with strikes 200-550
params = {
    "contract_type": "call",
    "expiration_date.gte": "2027-04-01",
    "expiration_date.lte": "2027-07-31",
    "strike_price.gte": 200,
    "strike_price.lte": 550,
    "limit": 250,
    "apiKey": API,
}

all_results = []
url = base
while True:
    r = requests.get(url, params=params if url == base else None, timeout=30)
    r.raise_for_status()
    data = r.json()
    all_results.extend(data.get("results", []))
    next_url = data.get("next_url")
    if next_url:
        url = next_url + f"&apiKey={API}"
        params = None
    else:
        break

# Bucket by expiration
by_expiry = {}
for c in all_results:
    exp = c.get("details", {}).get("expiration_date")
    by_expiry.setdefault(exp, []).append(c)

# Save raw
date = datetime.now(timezone.utc).strftime("%Y%m%d")
raw_path = f"{OUT_DIR}/INTU_leaps_{date}.json"
with open(raw_path, "w") as f:
    json.dump({"fetched_at": datetime.now(timezone.utc).isoformat(), "results": all_results}, f, indent=2)
print(f"Saved {len(all_results)} contracts to {raw_path}")
print(f"Expirations available: {sorted(by_expiry.keys())}")

# Print a summary table for the closest-to-1-year expiry (target: May 21, 2027)
target = "2027-05-21"
best_exp = min(by_expiry.keys(), key=lambda d: abs((datetime.strptime(d, "%Y-%m-%d") - datetime.strptime(target, "%Y-%m-%d")).days)) if by_expiry else None
if not best_exp:
    print("No contracts found.")
    sys.exit(0)

print(f"\nBest LEAP expiry: {best_exp} ({(datetime.strptime(best_exp, '%Y-%m-%d') - datetime.now()).days} days out)")
print("\nstrike,bid,ask,mid,last,vol,oi,iv,delta,gamma,theta,vega,break_even,und_price")
rows = []
for c in sorted(by_expiry[best_exp], key=lambda x: x.get("details", {}).get("strike_price", 0)):
    s = c.get("details", {}).get("strike_price")
    q = c.get("last_quote", {}) or {}
    t = c.get("last_trade", {}) or {}
    d = c.get("day", {}) or {}
    g = c.get("greeks", {}) or {}
    iv = c.get("implied_volatility")
    oi = c.get("open_interest")
    und = c.get("underlying_asset", {}).get("price")
    be = c.get("break_even_price")
    bid = q.get("bid")
    ask = q.get("ask")
    mid = (bid + ask) / 2 if (bid is not None and ask is not None) else None
    last = t.get("price") or d.get("close")
    vol = d.get("volume")
    rows.append([s, bid, ask, mid, last, vol, oi, iv, g.get("delta"), g.get("gamma"), g.get("theta"), g.get("vega"), be, und])
    print(",".join(str(x) if x is not None else "" for x in rows[-1]))

# Save table
csv_path = f"{OUT_DIR}/INTU_leaps_{best_exp}_table.csv"
with open(csv_path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["strike", "bid", "ask", "mid", "last", "volume", "open_interest", "iv", "delta", "gamma", "theta", "vega", "break_even", "underlying_price"])
    w.writerows(rows)
print(f"\nSaved table to {csv_path}")
