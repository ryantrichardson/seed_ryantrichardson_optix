"""Probe every plausible 'dark pool / trades' endpoint to find what Ryan's plan unlocks."""
import os, requests, json

K = os.environ["MASSIVE_API_KEY"]
H = "https://api.massive.com"

probes = [
    # Trades-style endpoints
    ("/v3/trades/SHOP",                 {"timestamp": "2026-05-08", "limit": 3}),
    ("/v2/ticks/stocks/trades/SHOP/2026-05-08", {"limit": 3}),
    ("/v1/last/trade/SHOP",             {}),
    ("/v2/last/trade/SHOP",             {}),

    # Dark pool / off-exchange specific (if Massive added a derived endpoint)
    ("/stocks/v1/dark-pool",            {"ticker":"SHOP","date.gte":"2026-04-01","limit":3}),
    ("/stocks/v1/dark-pool/SHOP",       {"date.gte":"2026-04-01","limit":3}),
    ("/stocks/v1/off-exchange-volume",  {"ticker":"SHOP","limit":3}),
    ("/stocks/v1/trf-volume",           {"ticker":"SHOP","limit":3}),
    ("/v3/aggregates/dark",             {"ticker":"SHOP","limit":3}),
    ("/v3/dark-pool/SHOP",              {"limit":3}),

    # Aggs by tape / exchange (might give dark vs lit split)
    ("/v3/snapshot/locale/us/markets/stocks/tickers/SHOP", {}),
    ("/v1/marketstatus/now",            {}),

    # Reference to inspect what is included
    ("/v3/reference/exchanges",         {}),

    # Short-volume confirmation (we know this works)
    ("/stocks/v1/short-volume",         {"ticker":"SHOP","limit":1}),

    # Account info if exposed
    ("/v3/me",                          {}),
    ("/v1/me",                          {}),
    ("/account",                        {}),
]

for path, params in probes:
    p = dict(params); p["apiKey"] = K
    try:
        r = requests.get(H+path, params=p, timeout=30)
        body = ""
        if r.status_code != 200:
            try: body = r.json().get("message","")[:140]
            except: body = r.text[:140]
        else:
            try:
                j = r.json()
                if isinstance(j, dict):
                    if "results" in j and isinstance(j["results"], list):
                        body = f"OK n={len(j['results'])} keys={list((j['results'][0] or {}).keys())[:8] if j['results'] else []}"
                    else:
                        body = f"OK keys={list(j.keys())[:8]}"
                else:
                    body = f"OK type={type(j).__name__}"
            except: body = "OK (non-JSON)"
        print(f"{r.status_code:3}  {path:55s}  {body}")
    except Exception as e:
        print(f"ERR  {path:55s}  {e}")
