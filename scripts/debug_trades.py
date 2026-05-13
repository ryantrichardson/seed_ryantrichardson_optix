"""Probe trades endpoint to confirm we can pull a full day of trades with pagination."""
import os, sys, requests, time
from datetime import datetime, timedelta, timezone

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

# Find most recent weekday with data (skip weekends)
today = datetime.now(timezone.utc).date()
test_date = today - timedelta(days=1)
while test_date.weekday() >= 5:
    test_date -= timedelta(days=1)

print(f"Testing trades for SHOP on {test_date}")

# Use timestamp.gte and timestamp.lt to bound a single trading day
# Massive timestamps are nanoseconds since epoch
start_ns = int(datetime.combine(test_date, datetime.min.time(), timezone.utc).timestamp() * 1e9)
end_ns = int(datetime.combine(test_date + timedelta(days=1), datetime.min.time(), timezone.utc).timestamp() * 1e9)

url = f"{BASE}/v3/trades/SHOP"
params = {
    "timestamp.gte": start_ns,
    "timestamp.lt": end_ns,
    "limit": 50000,
    "order": "asc",
}

total_trades = 0
dark_trades = 0
trf_venues = {}
conditions_seen = set()
exchanges_seen = set()
page = 0
t0 = time.time()

while url and page < 20:  # safety cap
    r = S.get(url, params=params if page == 0 else None, timeout=60)
    if r.status_code != 200:
        print(f"Page {page} ERROR {r.status_code}: {r.text[:300]}")
        break
    j = r.json()
    results = j.get("results", [])
    total_trades += len(results)
    for tr in results:
        exch = tr.get("exchange")
        trf_id = tr.get("trf_id")
        exchanges_seen.add(exch)
        if tr.get("conditions"):
            for c in tr["conditions"]:
                conditions_seen.add(c)
        if exch == 4 and trf_id is not None:
            dark_trades += 1
            trf_venues[trf_id] = trf_venues.get(trf_id, 0) + 1
    next_url = j.get("next_url")
    if next_url and "apiKey" not in next_url and "apikey" not in next_url.lower():
        # Append auth via header (already set) — just follow
        url = next_url
    else:
        url = next_url
    page += 1
    print(f"Page {page}: +{len(results)} trades (total={total_trades})  next={'yes' if url else 'no'}")
    if not url:
        break

elapsed = time.time() - t0
print(f"\n=== SUMMARY for SHOP {test_date} ===")
print(f"Total trades:        {total_trades:,}")
print(f"Dark trades (exch=4 & trf_id): {dark_trades:,}")
print(f"Dark ratio:          {(dark_trades/total_trades*100) if total_trades else 0:.2f}%")
print(f"TRF venue split:     {trf_venues}")
print(f"Exchanges seen:      {sorted(e for e in exchanges_seen if e is not None)}")
print(f"Sample conditions:   {sorted(conditions_seen)[:30]}")
print(f"Pages fetched:       {page}")
print(f"Elapsed:             {elapsed:.1f}s")
