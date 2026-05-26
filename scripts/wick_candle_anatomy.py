"""
For each of the 14 TSLA wicks, pull all /v3/trades INSIDE the 5-minute candle
itself (wick_minute -> wick_minute+5min). Compute:

  - total volume + notional in the candle
  - dark pool volume + notional (exchange==4 AND trf_id present) + DPR%
  - count and notional of "block" prints (>= 10,000 shares OR >= $1M notional)
  - largest single print: size, price, condition codes, dark/lit, exchange/trf_id
  - prints at-or-beyond the wick extreme (the "is the wick naked?" test):
      * count, total size, total notional, max single print at/beyond extreme
      * how many of those are dark
  - average daily 5-min volume that session (for relative baseline)
  - distance from VWAP of the prints in the candle

Output: data/wick_candle_anatomy.csv  (one row per wick)
       data/wick_candle_anatomy.json (detailed per-wick blob)
"""
import os, sys, json, time, statistics
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict, Counter
import requests

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
ET = timezone(timedelta(hours=-4))
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

INFILE = Path("data/tsla_14_wicks.json")
OUT_CSV = Path("data/wick_candle_anatomy.csv")
OUT_JSON = Path("data/wick_candle_anatomy.json")


def fetch_trades(ticker: str, start_ns: int, end_ns: int) -> list:
    url = f"{BASE}/v3/trades/{ticker}"
    params = {"timestamp.gte": start_ns, "timestamp.lt": end_ns, "limit": 50000, "order": "asc"}
    out = []
    pages = 0
    while url and pages < 25:
        r = S.get(url, params=params if pages == 0 else None, timeout=90)
        if r.status_code == 429:
            time.sleep(2)
            continue
        if r.status_code != 200:
            print(f"  ! HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
            break
        j = r.json()
        out.extend(j.get("results", []))
        url = j.get("next_url")
        params = None
        pages += 1
    return out


def fetch_minute_bars_for_day(ticker: str, day: str) -> list:
    """Get all 1-min bars for the day to compute baseline 5-min volume."""
    r = S.get(f"{BASE}/v2/aggs/ticker/{ticker}/range/1/minute/{day}/{day}",
              params={"adjusted": "true", "sort": "asc", "limit": 50000}, timeout=60)
    if r.status_code != 200:
        return []
    return r.json().get("results", [])


def main():
    wicks = json.loads(INFILE.read_text())["wicks"]
    rows = []
    details = []

    for w in wicks:
        wid = w["id"]
        ticker = w["ticker"]
        direction = w["direction"]
        extreme = float(w["extreme"])

        dt_et = datetime.strptime(f"{w['date']} {w['time_et']}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
        candle_start_ns = int(dt_et.timestamp() * 1e9)
        candle_end_ns   = int((dt_et + timedelta(minutes=5)).timestamp() * 1e9)

        print(f"\n[{wid}] {ticker} {w['date']} {w['time_et']} {direction}->{extreme} ({w['body_color']}, {w['session']})", file=sys.stderr)

        trades = fetch_trades(ticker, candle_start_ns, candle_end_ns)
        print(f"      {len(trades)} trades in 5-min window", file=sys.stderr)

        # Day baseline (median 5-min RTH volume to normalize)
        day_bars = fetch_minute_bars_for_day(ticker, w["date"])
        # Aggregate to 5-min RTH volumes
        rth_5m_vols = []
        for i in range(0, len(day_bars), 5):
            chunk = day_bars[i:i+5]
            if not chunk:
                continue
            t_ms = chunk[0]["t"]
            bar_dt = datetime.fromtimestamp(t_ms / 1000, tz=ET)
            if not (9 <= bar_dt.hour < 16 or (bar_dt.hour == 16 and bar_dt.minute == 0)):
                continue
            rth_5m_vols.append(sum(b.get("v", 0) for b in chunk))
        median_5m_rth_vol = statistics.median(rth_5m_vols) if rth_5m_vols else 0

        # Process trades in candle
        total_size = 0
        total_notional = 0.0
        dark_size = 0
        dark_notional = 0.0
        block_count = 0
        block_size = 0
        block_notional = 0.0
        largest_print = None
        largest_size = 0
        at_extreme_count = 0
        at_extreme_size = 0
        at_extreme_notional = 0.0
        at_extreme_dark_size = 0
        at_extreme_max_single = 0
        cond_counter = Counter()
        prices_weighted = []  # (price, size)
        venue_size = defaultdict(int)
        trf_size = defaultdict(int)

        # "at-or-beyond extreme" tolerance: within 1 cent
        def at_extreme(price):
            if direction == "down":
                return price <= extreme + 0.01
            else:
                return price >= extreme - 0.01

        for tr in trades:
            size = tr.get("size") or 0
            price = tr.get("price") or 0
            if size == 0 or price == 0:
                continue
            notional = size * price
            total_size += size
            total_notional += notional
            prices_weighted.append((price, size))

            is_dark = tr.get("exchange") == 4 and tr.get("trf_id") is not None
            if is_dark:
                dark_size += size
                dark_notional += notional
                trf_size[tr.get("trf_id")] += size
            venue_size[tr.get("exchange")] += size

            # Conditions
            for c in (tr.get("conditions") or []):
                cond_counter[c] += 1

            # Blocks: >=10k shares or >=$1M notional
            if size >= 10_000 or notional >= 1_000_000:
                block_count += 1
                block_size += size
                block_notional += notional

            # Largest single print
            if size > largest_size:
                largest_size = size
                largest_print = {
                    "size": size, "price": price, "notional": notional,
                    "exchange": tr.get("exchange"), "trf_id": tr.get("trf_id"),
                    "conditions": tr.get("conditions") or [],
                    "is_dark": is_dark,
                    "timestamp_ns": tr.get("sip_timestamp") or tr.get("participant_timestamp"),
                }

            # At or beyond wick extreme
            if at_extreme(price):
                at_extreme_count += 1
                at_extreme_size += size
                at_extreme_notional += notional
                if is_dark:
                    at_extreme_dark_size += size
                if size > at_extreme_max_single:
                    at_extreme_max_single = size

        # VWAP within candle
        vwap = (sum(p * s for p, s in prices_weighted) / sum(s for _, s in prices_weighted)) if prices_weighted else 0
        dist_extreme_from_vwap = (extreme - vwap) if vwap else None
        dist_extreme_from_vwap_pct = (dist_extreme_from_vwap / vwap * 100) if vwap else None

        # Relative to median 5-min RTH volume
        vol_ratio = (total_size / median_5m_rth_vol) if median_5m_rth_vol else None

        # DPR / "block dark"
        dpr_pct = (dark_notional / total_notional * 100) if total_notional else 0
        top_conds = cond_counter.most_common(5)
        top_trf = sorted(trf_size.items(), key=lambda x: -x[1])[:3]

        row = {
            "id": wid, "ticker": ticker, "date": w["date"], "time_et": w["time_et"],
            "direction": direction, "body_color": w["body_color"], "session": w["session"],
            "extreme": extreme,
            "trades_count": len(trades),
            "total_size": total_size,
            "total_notional_M": round(total_notional / 1e6, 3),
            "vwap": round(vwap, 4) if vwap else None,
            "dist_extreme_from_vwap_pct": round(dist_extreme_from_vwap_pct, 3) if dist_extreme_from_vwap_pct is not None else None,
            "median_5m_rth_vol": median_5m_rth_vol,
            "vol_vs_median_rth_x": round(vol_ratio, 2) if vol_ratio else None,
            "dark_size": dark_size,
            "dark_notional_M": round(dark_notional / 1e6, 3),
            "dpr_pct": round(dpr_pct, 2),
            "block_count": block_count,
            "block_size": block_size,
            "block_notional_M": round(block_notional / 1e6, 3),
            "largest_size": largest_size,
            "largest_price": largest_print["price"] if largest_print else None,
            "largest_is_dark": largest_print["is_dark"] if largest_print else None,
            "largest_conds": ",".join(str(c) for c in largest_print["conditions"]) if largest_print else "",
            "at_extreme_count": at_extreme_count,
            "at_extreme_size": at_extreme_size,
            "at_extreme_notional_K": round(at_extreme_notional / 1e3, 2),
            "at_extreme_dark_size": at_extreme_dark_size,
            "at_extreme_max_single": at_extreme_max_single,
            "top_conditions": ";".join(f"{c}:{n}" for c, n in top_conds),
            "top_trf_ids": ";".join(f"{t}:{s}" for t, s in top_trf),
        }
        rows.append(row)
        details.append({**row, "_largest_print_full": largest_print})

        time.sleep(0.15)  # gentle rate-limit

    # Write outputs
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    import csv
    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    OUT_JSON.write_text(json.dumps(details, indent=2, default=str))

    print(f"\nWrote {OUT_CSV} ({len(rows)} rows)", file=sys.stderr)
    print(f"Wrote {OUT_JSON}", file=sys.stderr)

    # Print summary table to stdout
    print("\n=== CANDLE ANATOMY SUMMARY ===")
    print(f"{'ID':>3} {'Date':10} {'Time':5} {'Dir':4} {'Color':5} {'Sess':4} {'Trades':>7} {'Vol':>10} {'Vol/Med':>8} {'$M':>7} {'DPR%':>6} {'Blocks':>6} {'BlockM':>7} {'MaxPrt':>7} {'MaxDk':>6} {'@Ext#':>6} {'@ExtSz':>7} {'@ExtMax':>8}")
    for r in rows:
        print(f"{r['id']:>3} {r['date']:10} {r['time_et']:5} {r['direction']:<4} {r['body_color']:<5} {r['session']:<4} "
              f"{r['trades_count']:>7,} {r['total_size']:>10,} {(r['vol_vs_median_rth_x'] or 0):>8.2f} "
              f"{r['total_notional_M']:>7.2f} {r['dpr_pct']:>6.1f} {r['block_count']:>6} {r['block_notional_M']:>7.2f} "
              f"{r['largest_size']:>7,} {str(r['largest_is_dark'])[:5]:>6} "
              f"{r['at_extreme_count']:>6} {r['at_extreme_size']:>7,} {r['at_extreme_max_single']:>8,}")


if __name__ == "__main__":
    main()
