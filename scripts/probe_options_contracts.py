"""
Probe the Massive options contracts reference API to find the right call
that returns historical expired contracts for QQQ on Jan 2025.
"""
import os
import requests
import json

KEY = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
session = requests.Session()
session.headers.update({"Authorization": f"Bearer {KEY}"})

# A real PBAR: QQQ 2024-12-11, target_exp = first Friday >= 2024-12-11 + 21 days = 2025-01-03
# Actually 2024-12-11 + 21 = 2025-01-01 (Wed), next Friday = 2025-01-03
test_cases = [
    # (label, params)
    ("no_filters", {"underlying_ticker": "QQQ", "expiration_date": "2025-01-03", "contract_type": "call", "limit": 5}),
    ("expired_true", {"underlying_ticker": "QQQ", "expiration_date": "2025-01-03", "contract_type": "call", "expired": "true", "limit": 5}),
    ("expired_true_asof", {"underlying_ticker": "QQQ", "expiration_date": "2025-01-03", "contract_type": "call", "expired": "true", "as_of": "2024-12-11", "limit": 5}),
    ("asof_only", {"underlying_ticker": "QQQ", "expiration_date": "2025-01-03", "contract_type": "call", "as_of": "2024-12-11", "limit": 5}),
    ("expired_true_asof_expdate", {"underlying_ticker": "QQQ", "expiration_date": "2025-01-03", "contract_type": "call", "expired": "true", "as_of": "2025-01-03", "limit": 5}),
    # Try without expiration_date
    ("range_expired", {"underlying_ticker": "QQQ", "expiration_date.gte": "2025-01-01", "expiration_date.lte": "2025-01-10", "contract_type": "call", "expired": "true", "as_of": "2024-12-11", "limit": 5}),
    # Maybe they use a different param style
    ("no_underlying_filter", {"expiration_date": "2025-01-03", "contract_type": "call", "expired": "true", "as_of": "2024-12-11", "limit": 5}),
]

url = f"{BASE}/v3/reference/options/contracts"
for label, params in test_cases:
    print(f"\n=== {label} ===")
    print(f"params: {params}")
    r = session.get(url, params=params, timeout=30)
    print(f"status: {r.status_code}")
    print(f"final url: {r.url}")
    try:
        j = r.json()
        results = j.get("results", [])
        print(f"results count: {len(results)} / total status: {j.get('status')}")
        if results:
            for c in results[:3]:
                print(f"  {c.get('ticker')} strike={c.get('strike_price')} exp={c.get('expiration_date')}")
        else:
            # show raw response
            print(f"  raw: {json.dumps(j)[:500]}")
    except Exception as e:
        print(f"  err: {e}; body: {r.text[:500]}")
