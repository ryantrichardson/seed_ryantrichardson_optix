"""
For each wick, search a WIDER window (±30 min around the recorded wick minute)
to find where the wick extreme actually appears in the trade tape. This
diagnoses timezone offset, DST, or 1m vs 5m alignment issues.
"""
import os, json, time
from pathlib import Path
from datetime import datetime, timedelta, timezone
import requests

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
ET = timezone(timedelta(hours=-4))
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

INFILE = Path("data/tsla_14_wicks.json")


def fetch_trades(ticker, s_ns, e_ns):
    url = f"{BASE}/v3/trades/{ticker}"
    params = {"timestamp.gte": s_ns, "timestamp.lt": e_ns, "limit": 50000, "order": "asc"}
    out = []
    pages = 0
    while url and pages < 30:
        r = S.get(url, params=params if pages == 0 else None, timeout=90)
        if r.status_code != 200:
            break
        j = r.json()
        out.extend(j.get("results", []))
        url = j.get("next_url")
        params = None
        pages += 1
    return out


def main():
    wicks = json.loads(INFILE.read_text())["wicks"]
    for w in wicks:
        direction = w["direction"]
        extreme = float(w["extreme"])
        dt_et = datetime.strptime(f"{w['date']} {w['time_et']}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
        # ±30 min window
        s_ns = int((dt_et - timedelta(minutes=30)).timestamp() * 1e9)
        e_ns = int((dt_et + timedelta(minutes=30)).timestamp() * 1e9)
        trades = fetch_trades(w["ticker"], s_ns, e_ns)

        # Find prints AT or BEYOND the extreme (tolerance: 0.05)
        if direction == "down":
            matches = [(t.get("sip_timestamp") or t.get("participant_timestamp"), t.get("price"), t.get("size"), t)
                       for t in trades if (t.get("price") or 9999) <= extreme + 0.05]
        else:
            matches = [(t.get("sip_timestamp") or t.get("participant_timestamp"), t.get("price"), t.get("size"), t)
                       for t in trades if (t.get("price") or 0) >= extreme - 0.05]

        prices = [t.get("price") for t in trades if t.get("price")]
        if not prices:
            print(f"[{w['id']}] no trades in ±30min")
            continue
        actual_lo = min(prices)
        actual_hi = max(prices)
        print(f"\n[{w['id']}] {w['date']} {w['time_et']} ET  dir={direction}  extreme={extreme}")
        print(f"     ±30min actual range: {actual_lo} - {actual_hi}   prints near extreme: {len(matches)}")
        # Show first 3 matches
        for ts_ns, px, sz, t in matches[:5]:
            if ts_ns:
                ts_et = datetime.fromtimestamp(int(ts_ns) / 1e9, tz=ET).strftime("%H:%M:%S.%f")[:-3]
            else:
                ts_et = "?"
            is_dark = t.get("exchange") == 4 and t.get("trf_id") is not None
            conds = t.get("conditions") or []
            print(f"       {ts_et} ET  px={px:>8.4f}  sz={sz:>7,}  dark={is_dark!s:5}  exch={t.get('exchange'):>2}  conds={conds}")
        time.sleep(0.15)


if __name__ == "__main__":
    main()
