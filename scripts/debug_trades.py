"""Probe which endpoints Ryan's plan covers."""
import os, requests

API_KEY = os.environ["MASSIVE_API_KEY"]
H = "https://api.massive.com"

probes = [
    ("/stocks/v1/short-volume/SHOP", {"date.gte": "2026-05-01", "date.lte": "2026-05-08", "limit": 5}),
    ("/stocks/v1/short-interest/SHOP", {"settlement_date.gte": "2026-04-01", "limit": 5}),
    ("/v3/short-volume/SHOP", {"date.gte": "2026-05-01", "limit": 5}),
    ("/v3/short-interest/SHOP", {"limit": 5}),
    ("/v3/reference/short-volume", {"ticker": "SHOP", "limit": 5}),
    ("/v3/reference/short-interest", {"ticker": "SHOP", "limit": 5}),
    ("/v2/aggs/ticker/SHOP/range/1/day/2026-05-01/2026-05-08", {"adjusted":"true","limit":10}),
    ("/v3/snapshot/options/SHOP", {"limit": 5}),
    ("/v3/reference/options/contracts", {"underlying_ticker":"SHOP","limit":5}),
]

for path, params in probes:
    params = dict(params); params["apiKey"] = API_KEY
    r = requests.get(H+path, params=params, timeout=30)
    msg = ""
    if r.status_code != 200:
        try: msg = r.json().get("message","")[:120]
        except: msg = r.text[:120]
    else:
        try:
            j = r.json()
            n = len(j.get("results") or j.get("data") or [])
            msg = f"OK results={n}"
        except: msg = "OK (non-JSON)"
    print(f"{r.status_code}  {path:55s}  {msg}")
