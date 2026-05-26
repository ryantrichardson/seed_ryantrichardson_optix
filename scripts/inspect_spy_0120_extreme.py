"""Inspect SPY 2026-01-20 prints at/near 691.6085 across the whole day to see
what kind of trades they are (conditions, exchange, size) and whether each
candle that hits this extreme has a fresh print or whether we're just dragging
the same extreme forward through the trades feed."""
import os, sys
from datetime import datetime, timedelta, timezone
import requests

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
ET = timezone(timedelta(hours=-5))
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

TICKER = "SPY"
DAY = datetime(2026, 1, 20, tzinfo=ET)
TARGET = 691.6085
TOL = 0.05

def fetch_all_trades(ticker, s_ns, e_ns):
    url = f"{BASE}/v3/trades/{ticker}"
    params = {"timestamp.gte": s_ns, "timestamp.lt": e_ns, "limit": 50000, "order": "asc"}
    out, pages = [], 0
    while url and pages < 400:
        for attempt in range(5):
            try:
                r = S.get(url, params=params if pages == 0 else None, timeout=120)
                if r.status_code != 200:
                    print(f"HTTP {r.status_code}: {r.text[:200]}")
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
    d_start = DAY.replace(hour=4, minute=0)
    d_end = DAY.replace(hour=20, minute=0)
    trades = fetch_all_trades(TICKER, int(d_start.timestamp()*1e9), int(d_end.timestamp()*1e9))
    print(f"Total trades: {len(trades):,}\n")

    # Find prints at/near target
    near = [t for t in trades if abs((t.get("price") or 0) - TARGET) <= TOL]
    print(f"Prints within ${TOL} of {TARGET}: {len(near)}\n")

    print("First 50 such prints (sip_timestamp ET | participant_timestamp ET | price | size | exch | trf | conds):")
    for t in near[:50]:
        sip = int(t.get("sip_timestamp") or 0)
        par = int(t.get("participant_timestamp") or 0)
        sip_dt = datetime.fromtimestamp(sip/1e9, tz=ET) if sip else None
        par_dt = datetime.fromtimestamp(par/1e9, tz=ET) if par else None
        print(f"  sip={sip_dt.strftime('%H:%M:%S.%f')[:-3] if sip_dt else '-'}  "
              f"par={par_dt.strftime('%H:%M:%S.%f')[:-3] if par_dt else '-'}  "
              f"px={t.get('price')}  sz={t.get('size')}  "
              f"exch={t.get('exchange')}  trf={t.get('trf_id')}  "
              f"conds={t.get('conditions')}")

    # group by sip_minute
    from collections import Counter
    by_sip_min = Counter()
    by_par_min = Counter()
    for t in near:
        sip = int(t.get("sip_timestamp") or 0)
        par = int(t.get("participant_timestamp") or 0)
        if sip:
            sip_dt = datetime.fromtimestamp(sip/1e9, tz=ET)
            by_sip_min[sip_dt.strftime("%H:%M")] += 1
        if par:
            par_dt = datetime.fromtimestamp(par/1e9, tz=ET)
            by_par_min[par_dt.strftime("%H:%M")] += 1

    print(f"\nDistinct sip minutes carrying a ~{TARGET} print: {len(by_sip_min)}")
    print(f"Top sip minutes: {by_sip_min.most_common(10)}")
    print(f"\nDistinct participant minutes: {len(by_par_min)}")
    print(f"Top participant minutes: {by_par_min.most_common(10)}")


if __name__ == "__main__":
    main()
