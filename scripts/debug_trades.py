import os, requests, json
K = os.environ["MASSIVE_API_KEY"]
for path, params in [
    ("/stocks/v1/short-volume", {"ticker": "SHOP", "date.gte": "2026-04-01", "date.lte": "2026-05-08", "limit": 5, "order":"asc"}),
    ("/stocks/v1/short-interest", {"ticker": "SHOP", "limit": 3}),
]:
    p = dict(params); p["apiKey"] = K
    r = requests.get("https://api.massive.com"+path, params=p, timeout=30)
    print(f"\n=== {path} -> {r.status_code} ===")
    if r.status_code == 200:
        j = r.json()
        print(f"keys: {list(j.keys())}")
        results = j.get("results") or []
        print(f"count: {len(results)}")
        if results: print(f"first: {json.dumps(results[0], indent=2)[:1200]}")
    else:
        print(r.text[:400])
