"""Compare the prints at the extreme of each SPY Jan 2026 PBAR candidate to
find what distinguishes the 3 ghost bars (invisible on ToS) from the 6 real
PBARs (visible on ToS).

GHOSTS (per Ryan): 01-02 08:20 down, 01-15 08:00 down, 01-20 15:40 up
PBARS (visible):   01-06 17:00 down, 01-09 16:30 + 16:35 down,
                   01-14 16:10 up, 01-20 04:00 up, 01-21 16:10 down
"""
import os, sys
from datetime import datetime, timedelta, timezone
from collections import Counter
import requests

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
ET = timezone(timedelta(hours=-5))
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

TICKER = "SPY"
TOL = 0.05

# (date, hour, minute, direction, extreme, label)
CANDIDATES = [
    ("2026-01-02", 8, 20,  "down", 681.92,  "GHOST"),
    ("2026-01-15", 8, 0,   "down", 689.50,  "GHOST"),
    ("2026-01-20", 15, 40, "up",   681.6845,"GHOST"),
    ("2026-01-06", 17, 0,  "down", 687.86,  "PBAR"),
    ("2026-01-09", 16, 30, "down", 689.6022,"PBAR"),
    ("2026-01-09", 16, 35, "down", 689.6022,"PBAR"),
    ("2026-01-14", 16, 10, "up",   693.8932,"PBAR"),
    ("2026-01-20", 4, 0,   "up",   685.86,  "PBAR"),
    ("2026-01-21", 16, 10, "down", 677.58,  "PBAR"),
]


def fetch_trades_window(start_dt, end_dt):
    s_ns = int(start_dt.timestamp()*1e9)
    e_ns = int(end_dt.timestamp()*1e9)
    url = f"{BASE}/v3/trades/{TICKER}"
    params = {"timestamp.gte": s_ns, "timestamp.lt": e_ns, "limit": 50000, "order": "asc"}
    out, pages = [], 0
    while url and pages < 20:
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


def main():
    print(f"{'='*100}")
    print(f"Comparing 3 GHOST bars vs 6 visible PBARs on SPY Jan 2026")
    print(f"{'='*100}\n")

    for date_str, h, m, direction, extreme, label in CANDIDATES:
        y, mo, d = map(int, date_str.split("-"))
        bkt_start = datetime(y, mo, d, h, m, 0, tzinfo=ET)
        bkt_end = bkt_start + timedelta(minutes=5)
        trades = fetch_trades_window(bkt_start, bkt_end)

        # Filter to prints near the extreme (use sip_timestamp to match scanner)
        if direction == "down":
            near = [t for t in trades if (t.get("price") or 1e9) <= extreme + TOL]
        else:
            near = [t for t in trades if (t.get("price") or 0) >= extreme - TOL]

        # Sort by sip
        near.sort(key=lambda t: int(t.get("sip_timestamp") or 0))

        print(f"\n--- {label}  {date_str} {h:02d}:{m:02d} ET  {direction}  ext={extreme} ---")
        print(f"  Trades in 5-min window: {len(trades):,}    Prints at extreme: {len(near)}")
        # Aggregate stats
        exch_ct = Counter(t.get("exchange") for t in near)
        trf_ct = Counter(t.get("trf_id") for t in near)
        cond_ct = Counter()
        for t in near:
            for c in (t.get("conditions") or []):
                cond_ct[c] += 1
        sizes = [t.get("size") or 0 for t in near]
        total_sz = sum(sizes)
        print(f"  Exchanges: {dict(exch_ct)}")
        print(f"  TRF IDs:   {dict(trf_ct)}")
        print(f"  Conditions:{dict(cond_ct)}")
        print(f"  Sizes:     count={len(sizes)} sum={total_sz:,} max={max(sizes) if sizes else 0:,}")
        # Show every print
        print(f"  All prints at extreme:")
        for t in near:
            sip = int(t.get("sip_timestamp") or 0)
            par = int(t.get("participant_timestamp") or 0)
            sip_dt = datetime.fromtimestamp(sip/1e9, tz=ET) if sip else None
            par_dt = datetime.fromtimestamp(par/1e9, tz=ET) if par else None
            lag_ms = (sip - par)/1e6 if sip and par else 0
            sip_s = sip_dt.strftime('%H:%M:%S.%f')[:-3] if sip_dt else '-'
            par_s = par_dt.strftime('%H:%M:%S.%f')[:-3] if par_dt else '-'
            print(f"    sip={sip_s}  par={par_s}  lag={lag_ms:+.0f}ms  "
                  f"px={t.get('price')}  sz={t.get('size'):,}  "
                  f"exch={t.get('exchange')}  trf={t.get('trf_id')}  "
                  f"conds={t.get('conditions')}  id={t.get('id','')[:12]}")


if __name__ == "__main__":
    main()
