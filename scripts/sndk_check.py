"""Verify what SNDK is on Massive right now."""
import os, requests
from datetime import datetime, timezone, timedelta
API = os.environ["MASSIVE_API_KEY"]
S = requests.Session(); S.headers.update({"Authorization": f"Bearer {API}"})

# Ticker details
r = S.get("https://api.massive.com/v3/reference/tickers/SNDK", timeout=30)
print("REFERENCE:", r.status_code)
print(r.json())
print()

# Recent daily bars
end = datetime.now(timezone.utc).date()
start = end - timedelta(days=20)
r = S.get(f"https://api.massive.com/v2/aggs/ticker/SNDK/range/1/day/{start}/{end}",
          params={"adjusted":"true","sort":"asc"}, timeout=30)
j = r.json()
print(f"DAILY BARS:")
print(f"{'DATE':<12} {'OPEN':>10} {'HIGH':>10} {'LOW':>10} {'CLOSE':>10} {'VOL':>14} {'$VOL_M':>12}")
for b in j.get("results",[]):
    d = datetime.fromtimestamp(b['t']/1000, timezone.utc).date()
    dollar_vol = (b.get('vw') or b['c']) * b['v'] / 1e6
    print(f"{str(d):<12} {b['o']:>10.2f} {b['h']:>10.2f} {b['l']:>10.2f} {b['c']:>10.2f} {b['v']:>14,.0f} {dollar_vol:>12,.0f}")
