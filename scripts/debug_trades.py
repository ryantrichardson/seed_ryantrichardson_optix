"""Temporary diagnostic - figure out why /v3/trades is failing."""
import os
import requests
import json

API_KEY = os.environ["MASSIVE_API_KEY"]

# Test 1: trades endpoint with a known date for SHOP
print("=== TEST 1: /v3/trades/SHOP for 2026-05-08 ===")
url = "https://api.massive.com/v3/trades/SHOP"
params = {"timestamp": "2026-05-08", "limit": 100, "order": "asc", "sort": "timestamp", "apiKey": API_KEY}
r = requests.get(url, params=params, timeout=60)
print(f"Status: {r.status_code}")
print(f"URL: {r.url.replace(API_KEY, '***')}")
print(f"Body (first 1500 chars): {r.text[:1500]}")
print()

# Test 2: try the Polygon URL (massive may be a brand we got wrong)
print("=== TEST 2: same call against api.polygon.io ===")
url2 = "https://api.polygon.io/v3/trades/SHOP"
r2 = requests.get(url2, params=params, timeout=60)
print(f"Status: {r2.status_code}")
print(f"Body (first 1500 chars): {r2.text[:1500]}")
print()

# Test 3: subscription info
print("=== TEST 3: account/subscription metadata ===")
for path in ["/v3/reference/tickers/SHOP", "/v1/marketstatus/now"]:
    for host in ["https://api.massive.com", "https://api.polygon.io"]:
        try:
            r3 = requests.get(f"{host}{path}", params={"apiKey": API_KEY}, timeout=30)
            print(f"{host}{path} -> {r3.status_code}")
            if r3.status_code != 200:
                print(f"  body: {r3.text[:300]}")
        except Exception as e:
            print(f"{host}{path} -> ERROR {e}")
