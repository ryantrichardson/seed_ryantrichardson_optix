"""
For each wick, pull all trades in the 5-min candle window and bucket them by price
distance from the wick extreme. This shows:
  - how the trade volume is distributed within the candle
  - whether real volume reached the extreme tip vs just one tiny print
  - what the largest print near the tip looked like

Output: prints a per-wick distribution table.
"""
import os, sys, json, time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import Counter
import requests

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
ET = timezone(timedelta(hours=-4))
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

INFILE = Path("data/tsla_14_wicks.json")
OUT_JSON = Path("data/wick_print_distribution.json")


def fetch_trades(ticker, start_ns, end_ns):
    url = f"{BASE}/v3/trades/{ticker}"
    params = {"timestamp.gte": start_ns, "timestamp.lt": end_ns, "limit": 50000, "order": "asc"}
    out = []
    pages = 0
    while url and pages < 25:
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
    all_results = []

    for w in wicks:
        wid = w["id"]
        direction = w["direction"]
        extreme = float(w["extreme"])
        wick_high = float(w["high"])
        wick_low  = float(w["low"])
        wick_open = float(w["open"])
        wick_close= float(w["close"])

        dt_et = datetime.strptime(f"{w['date']} {w['time_et']}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
        s_ns = int(dt_et.timestamp() * 1e9)
        e_ns = int((dt_et + timedelta(minutes=5)).timestamp() * 1e9)

        trades = fetch_trades(w["ticker"], s_ns, e_ns)
        print(f"\n[{wid}] {w['ticker']} {w['date']} {w['time_et']} {direction}->{extreme} ({w['body_color']}, {w['session']})")
        print(f"     screenshot OHLC: O={wick_open} H={wick_high} L={wick_low} C={wick_close}")
        print(f"     {len(trades)} trades fetched")

        if not trades:
            all_results.append({"id": wid, "trades": 0, "actual_low": None, "actual_high": None, "tip_prints": []})
            continue

        sizes = [(t.get("price") or 0, t.get("size") or 0, t) for t in trades]
        sizes = [(p, s, t) for p, s, t in sizes if p > 0 and s > 0]
        actual_low = min(p for p, s, t in sizes)
        actual_high = max(p for p, s, t in sizes)
        total_vol = sum(s for p, s, t in sizes)

        # Determine wick range in absolute terms
        wick_range = wick_high - wick_low
        # Buckets relative to wick range from extreme
        # Bin distance from extreme into 5 buckets: 0-10%, 10-25%, 25-50%, 50-75%, 75-100% of wick range
        # plus "beyond extreme" (shouldn't happen if extreme = true low/high) and "in body"
        if direction == "down":
            ref = wick_low
            def bucket_of(p):
                if p < ref - 0.005:
                    return "beyond_extreme"
                dist_from_ext = p - ref  # 0 at extreme, larger as we go up
                pct = dist_from_ext / wick_range * 100 if wick_range else 100
                if pct <= 5:    return "0-5%_of_wick"      # tip
                if pct <= 15:   return "5-15%_of_wick"
                if pct <= 30:   return "15-30%_of_wick"
                if pct <= 50:   return "30-50%_of_wick"
                return "50-100%_of_wick_or_body"
        else:
            ref = wick_high
            def bucket_of(p):
                if p > ref + 0.005:
                    return "beyond_extreme"
                dist_from_ext = ref - p
                pct = dist_from_ext / wick_range * 100 if wick_range else 100
                if pct <= 5:    return "0-5%_of_wick"
                if pct <= 15:   return "5-15%_of_wick"
                if pct <= 30:   return "15-30%_of_wick"
                if pct <= 50:   return "30-50%_of_wick"
                return "50-100%_of_wick_or_body"

        bucket_vol = Counter()
        bucket_count = Counter()
        bucket_dark_vol = Counter()
        for p, s, t in sizes:
            b = bucket_of(p)
            bucket_vol[b] += s
            bucket_count[b] += 1
            is_dark = t.get("exchange") == 4 and t.get("trf_id") is not None
            if is_dark:
                bucket_dark_vol[b] += s

        print(f"     actual price range in 5-min: {actual_low} - {actual_high}  (vol {total_vol:,})")
        print(f"     Price bucket (from extreme {ref}, wick range {wick_range:.2f}):")
        order = ["beyond_extreme", "0-5%_of_wick", "5-15%_of_wick", "15-30%_of_wick", "30-50%_of_wick", "50-100%_of_wick_or_body"]
        for b in order:
            if bucket_count[b] == 0:
                continue
            v = bucket_vol[b]
            dv = bucket_dark_vol[b]
            print(f"       {b:>26}  count={bucket_count[b]:>6}  size={v:>9,}  dark={dv:>9,} ({dv/v*100 if v else 0:5.1f}%)")

        # Top 3 prints closest to extreme
        sorted_by_dist = sorted(sizes, key=lambda x: abs(x[0] - ref))
        tip_prints = []
        for p, s, t in sorted_by_dist[:5]:
            is_dark = t.get("exchange") == 4 and t.get("trf_id") is not None
            tip_prints.append({"price": p, "size": s, "is_dark": is_dark, "exchange": t.get("exchange"), "trf_id": t.get("trf_id"), "conditions": t.get("conditions") or []})

        # Largest 3 prints anywhere in candle
        sorted_by_size = sorted(sizes, key=lambda x: -x[1])
        largest = []
        for p, s, t in sorted_by_size[:3]:
            is_dark = t.get("exchange") == 4 and t.get("trf_id") is not None
            largest.append({"price": p, "size": s, "is_dark": is_dark, "exchange": t.get("exchange"), "trf_id": t.get("trf_id"), "conditions": t.get("conditions") or []})

        print(f"     5 prints closest to extreme:")
        for tp in tip_prints:
            print(f"       price={tp['price']:>8.4f}  size={tp['size']:>7,}  dark={tp['is_dark']!s:5}  conds={tp['conditions']}")

        print(f"     3 largest prints in candle:")
        for tp in largest:
            print(f"       price={tp['price']:>8.4f}  size={tp['size']:>7,}  dark={tp['is_dark']!s:5}  conds={tp['conditions']}")

        all_results.append({
            "id": wid, "ticker": w["ticker"], "date": w["date"], "time_et": w["time_et"],
            "direction": direction, "body_color": w["body_color"], "session": w["session"],
            "screenshot_OHLC": {"o": wick_open, "h": wick_high, "l": wick_low, "c": wick_close},
            "extreme": extreme, "actual_low": actual_low, "actual_high": actual_high,
            "trades": len(trades), "total_size": total_vol,
            "bucket_vol": dict(bucket_vol), "bucket_count": dict(bucket_count), "bucket_dark_vol": dict(bucket_dark_vol),
            "tip_prints": tip_prints, "largest_prints": largest,
        })

        time.sleep(0.15)

    OUT_JSON.write_text(json.dumps(all_results, indent=2, default=str))
    print(f"\nWrote {OUT_JSON}")


if __name__ == "__main__":
    main()
