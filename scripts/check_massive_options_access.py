"""Probe Massive API to check what options data access this API key has.

Tests, in order of usefulness for our backtest:
1. List active SPY option contracts (reference data) - cheapest
2. Get historical option contract details for a known SPY option
3. Pull historical option trades for that contract on a recent date
4. Pull historical option aggregates (1-min bars) for that contract
5. Pull option chain snapshot (current state)
6. Pull option snapshot quote (NBBO) on a historical date
"""
import os, json
from datetime import datetime, timedelta
import requests

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})


def probe(label, url, params=None):
    print(f"\n=== {label} ===")
    print(f"GET {url}")
    if params:
        print(f"params: {params}")
    try:
        r = S.get(url, params=params, timeout=30)
        print(f"status: {r.status_code}")
        body = r.text[:1500]
        try:
            j = r.json()
            # show top-level keys and one sample record
            print(f"top-level keys: {list(j.keys()) if isinstance(j, dict) else 'list'}")
            if isinstance(j, dict):
                if "results" in j:
                    res = j["results"]
                    if isinstance(res, list):
                        print(f"results count: {len(res)}")
                        if res:
                            print(f"sample result: {json.dumps(res[0], indent=2)[:600]}")
                    else:
                        print(f"results: {json.dumps(res, indent=2)[:800]}")
                else:
                    print(f"body: {json.dumps(j, indent=2)[:800]}")
            else:
                print(f"body: {body}")
        except Exception:
            print(f"body (non-json): {body}")
        return r.status_code, r
    except Exception as e:
        print(f"ERROR: {e}")
        return None, None


def main():
    print("Probing Massive API options access...")
    print(f"Base: {BASE}")
    print(f"Key prefix: {API[:8]}...{API[-4:]}")

    # 1. Reference data: list SPY option contracts (active)
    probe(
        "1. Reference: active SPY options (cheapest probe)",
        f"{BASE}/v3/reference/options/contracts",
        {"underlying_ticker": "SPY", "limit": 5, "expired": "false"}
    )

    # 2. Pick a known SPY weekly that should have existed 2025-11-17 (a Friday close)
    # Format: O:SPY{YYMMDD}{C|P}{strike*1000 padded 8}
    # Use SPY 2025-11-21 expiry, 690 call (close to ATM at that time)
    contract = "O:SPY251121C00690000"
    probe(
        f"2. Reference: contract details for {contract}",
        f"{BASE}/v3/reference/options/contracts/{contract}"
    )

    # 3. Historical trades for that contract on 2025-11-19 (a recent pbar day)
    day_start = int(datetime(2025, 11, 19, 14, 0).timestamp() * 1e9)  # ~9am ET in UTC
    day_end = int(datetime(2025, 11, 19, 21, 0).timestamp() * 1e9)    # ~4pm ET in UTC
    probe(
        f"3. Historical trades for {contract} on 2025-11-19",
        f"{BASE}/v3/trades/{contract}",
        {"timestamp.gte": day_start, "timestamp.lt": day_end, "limit": 10}
    )

    # 4. Historical 1-min aggregates for that contract on 2025-11-19
    probe(
        f"4. Historical 1-min aggs for {contract} on 2025-11-19",
        f"{BASE}/v2/aggs/ticker/{contract}/range/1/minute/2025-11-19/2025-11-19",
        {"limit": 10}
    )

    # 5. Historical NBBO quotes (mid-day on 2025-11-19)
    quote_start = int(datetime(2025, 11, 19, 15, 30).timestamp() * 1e9)
    quote_end = int(datetime(2025, 11, 19, 15, 35).timestamp() * 1e9)
    probe(
        f"5. Historical quotes (NBBO) for {contract} on 2025-11-19 15:30 UTC",
        f"{BASE}/v3/quotes/{contract}",
        {"timestamp.gte": quote_start, "timestamp.lt": quote_end, "limit": 5}
    )

    # 6. Option chain snapshot (current state - tests snapshot tier)
    probe(
        "6. Snapshot: current SPY option chain",
        f"{BASE}/v3/snapshot/options/SPY",
        {"limit": 5}
    )

    # 7. Aggregates of underlying (we know this works - control test)
    probe(
        "7. CONTROL: SPY equity 1-day agg (should always work)",
        f"{BASE}/v2/aggs/ticker/SPY/range/1/day/2025-11-19/2025-11-19"
    )

    print("\n\n=== Summary ===")
    print("Any 200 on probes 2-6 means we have some level of options access.")
    print("Probes 3+4 = historical options trades/bars - needed for backtest precision.")
    print("Probe 5 = NBBO quotes - useful for realistic fill prices.")
    print("Probe 6 = current snapshot - tests live options tier.")
    print("403 / 401 / 'unauthorized' / 'not entitled' on options endpoints = no options tier.")


if __name__ == "__main__":
    main()
