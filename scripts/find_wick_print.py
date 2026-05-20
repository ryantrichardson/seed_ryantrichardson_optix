"""
Find any TSLA print at exactly $394.635 (or $394.63 / $394.64) on 5/19.
Also check 5/18 and 5/20 in case of timezone confusion.
And dump every print at the ToS open price (404.89) to see when that traded.
"""
import os, requests, time
from datetime import datetime, timezone, timedelta

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

def scan(date_str):
    """Scan all trades for a date, return list of all prints at 394.6x."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    start = datetime(d.year, d.month, d.day, 4, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
    end   = datetime(d.year, d.month, d.day, 20, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
    start_ns = int(start.timestamp() * 1e9)
    end_ns   = int(end.timestamp() * 1e9)

    print(f"\n=== Scanning {date_str} ({start.isoformat()} to {end.isoformat()}) ===")
    u = f"{BASE}/v3/trades/TSLA"
    p = {"timestamp.gte": start_ns, "timestamp.lt": end_ns, "limit": 50000, "order": "asc"}
    matches = []  # 394.50 - 394.80
    open_matches = []  # 404.85 - 404.95
    pages = 0
    total = 0
    while u and pages < 500:
        for attempt in range(5):
            try:
                r = S.get(u, params=p if pages == 0 else None, timeout=60)
                break
            except Exception:
                time.sleep(1 + attempt)
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}: {r.text[:200]}")
            break
        j = r.json()
        results = j.get("results", [])
        total += len(results)
        for t in results:
            px = t.get("price", 0)
            if 394.50 <= px <= 394.80:
                matches.append(t)
            if 404.85 <= px <= 404.95:
                open_matches.append(t)
        u = j.get("next_url"); p = None; pages += 1
    print(f"  total trades: {total}")
    print(f"  prints in $394.50-$394.80 range: {len(matches)}")
    print(f"  prints in $404.85-$404.95 range: {len(open_matches)}")

    if matches:
        # Show time distribution
        from collections import Counter
        hours = Counter()
        for t in matches:
            ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
            if ts_ns:
                ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(timezone(timedelta(hours=-4)))
                hours[f"{ts.hour:02d}:{ts.minute:02d}"] += 1
        print(f"  Top minutes for $394.5-$394.8 prints:")
        for k, v in sorted(hours.items(), key=lambda x: -x[1])[:10]:
            print(f"    {k} ET: {v} prints")

    if open_matches:
        from collections import Counter
        hours = Counter()
        for t in open_matches:
            ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
            if ts_ns:
                ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(timezone(timedelta(hours=-4)))
                hours[f"{ts.hour:02d}:{ts.minute:02d}"] += 1
        print(f"  Top minutes for $404.85-$404.95 prints:")
        for k, v in sorted(hours.items(), key=lambda x: -x[1])[:10]:
            print(f"    {k} ET: {v} prints")

for d in ["2026-05-18", "2026-05-19", "2026-05-20"]:
    try:
        scan(d)
    except Exception as e:
        print(f"  ERROR on {d}: {e}")
