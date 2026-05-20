"""Pull SLV daily bars 5/06 through today to see what actually happened post-5/12."""
import os, requests
from datetime import datetime, timezone, timedelta
API = os.environ["MASSIVE_API_KEY"]
S = requests.Session(); S.headers.update({"Authorization": f"Bearer {API}"})

end = datetime.now(timezone.utc).date()
start = datetime(2026, 5, 5).date()
r = S.get(f"https://api.massive.com/v2/aggs/ticker/SLV/range/1/day/{start}/{end}",
          params={"adjusted":"true","sort":"asc"}, timeout=60)
j = r.json()
print(f"{'DATE':<12} {'OPEN':>8} {'HIGH':>8} {'LOW':>8} {'CLOSE':>8} {'VOL':>14} {'CHG%':>7}")
prev = None
for b in j.get("results",[]):
    d = datetime.fromtimestamp(b['t']/1000, timezone.utc).date()
    chg = ""
    if prev: chg = f"{(b['c']/prev-1)*100:+.2f}%"
    print(f"{str(d):<12} {b['o']:>8.2f} {b['h']:>8.2f} {b['l']:>8.2f} {b['c']:>8.2f} {b['v']:>14,.0f} {chg:>7}")
    prev = b['c']
