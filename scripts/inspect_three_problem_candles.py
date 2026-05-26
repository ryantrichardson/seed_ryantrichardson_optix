"""Inspect 3 problem candles from the Oct-Nov 2025 SPY scan to understand
why the scanner classified them wrong, so we can refine the rules.

1) 2025-10-10 16:00 ET up   ext=653.02  -> Ryan says "no wick at all", false PBAR
2) 2025-11-19 10:15 ET down ext=660.80  -> should be GHOST (he can't see wick), worked great
3) 2025-11-21 11:50 ET down ext=651.215 -> Ryan says nothing there, false PBAR

For each: pull every trade in the 5-min window, list all prints at/near
the extreme with full detail (conditions, exchange, trf, lag, size).
Also list the next few prints inward from the extreme to see what's around it.
"""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import Counter
import requests

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
ET = ZoneInfo("America/New_York")
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

TICKER = "SPY"
TOL = 0.05

# (date, hour, minute, direction, extreme, label)
CASES = [
    ("2025-10-10", 16, 0,  "up",   653.02,   "FALSE-PBAR (no wick on chart)"),
    ("2025-11-19", 10, 15, "down", 660.80,   "SHOULD-BE-GHOST (invisible, perfect entry)"),
    ("2025-11-21", 11, 50, "down", 651.215,  "FALSE-PBAR (nothing there)"),
]


def fetch_trades(start_dt, end_dt):
    s_ns = int(start_dt.timestamp()*1e9)
    e_ns = int(end_dt.timestamp()*1e9)
    url = f"{BASE}/v3/trades/{TICKER}"
    params = {"timestamp.gte": s_ns, "timestamp.lt": e_ns, "limit": 50000, "order": "asc"}
    out, pages = [], 0
    while url and pages < 30:
        for attempt in range(5):
            try:
                r = S.get(url, params=params if pages == 0 else None, timeout=120)
                if r.status_code != 200:
                    print(f"  HTTP {r.status_code}: {r.text[:200]}")
                    return out
                j = r.json()
                out.extend(j.get("results", []))
                url = j.get("next_url")
                params = None
                pages += 1
                break
            except Exception as e:
                print(f"  retry {attempt}: {e}")
    return out


def fmt_ts(ns):
    if not ns:
        return "-"
    return datetime.fromtimestamp(int(ns)/1e9, tz=ET).strftime("%H:%M:%S.%f")[:-3]


def main():
    for date_str, h, m, direction, extreme, label in CASES:
        y, mo, d = map(int, date_str.split("-"))
        bkt_start = datetime(y, mo, d, h, m, 0, tzinfo=ET)
        bkt_end = bkt_start + timedelta(minutes=5)
        trades = fetch_trades(bkt_start, bkt_end)

        # All prints near the extreme
        if direction == "down":
            near = [t for t in trades if (t.get("price") or 1e9) <= extreme + TOL]
        else:
            near = [t for t in trades if (t.get("price") or 0) >= extreme - TOL]
        near.sort(key=lambda t: int(t.get("sip_timestamp") or 0))

        print(f"\n{'='*100}")
        print(f"{label}")
        print(f"  {date_str} {h:02d}:{m:02d} ET  {direction}  ext={extreme}")
        print(f"  Total trades in 5-min window: {len(trades):,}")
        print(f"  Prints at/near extreme: {len(near)}")

        # Stats
        exch_ct = Counter(t.get("exchange") for t in near)
        trf_ct = Counter(t.get("trf_id") for t in near)
        cond_ct = Counter()
        for t in near:
            for c in (t.get("conditions") or []):
                cond_ct[c] += 1
        sizes = [t.get("size") or 0 for t in near]
        total_sz = sum(sizes)
        print(f"  Total size at extreme: {total_sz:,}")
        print(f"  Exchanges:  {dict(exch_ct)}")
        print(f"  TRF IDs:    {dict(trf_ct)}")
        print(f"  Conditions: {dict(cond_ct)}")
        print(f"\n  Every print at extreme:")
        for t in near:
            sip = int(t.get("sip_timestamp") or 0)
            par = int(t.get("participant_timestamp") or 0)
            lag_ms = (sip - par)/1e6 if sip and par else 0
            print(f"    sip={fmt_ts(sip)}  par={fmt_ts(par)}  lag={lag_ms:+,.0f}ms  "
                  f"px={t.get('price')}  sz={(t.get('size') or 0):,}  "
                  f"exch={t.get('exchange')}  trf={t.get('trf_id')}  "
                  f"conds={t.get('conditions')}")

        # Also list a few nearest prints inside the extreme (to see how "alone" it is)
        if direction == "down":
            inside = sorted([t for t in trades if (t.get("price") or 0) > extreme + TOL],
                            key=lambda t: t.get("price") or 0)
            print(f"\n  5 closest prints ABOVE the low extreme (toward body):")
        else:
            inside = sorted([t for t in trades if (t.get("price") or 1e9) < extreme - TOL],
                            key=lambda t: -(t.get("price") or 0))
            print(f"\n  5 closest prints BELOW the high extreme (toward body):")
        for t in inside[:5]:
            sip = int(t.get("sip_timestamp") or 0)
            par = int(t.get("participant_timestamp") or 0)
            lag_ms = (sip - par)/1e6 if sip and par else 0
            print(f"    sip={fmt_ts(sip)}  px={t.get('price')}  sz={(t.get('size') or 0):,}  "
                  f"exch={t.get('exchange')}  trf={t.get('trf_id')}  "
                  f"conds={t.get('conditions')}  lag={lag_ms:+,.0f}ms")


if __name__ == "__main__":
    main()
