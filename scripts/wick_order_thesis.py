"""
TWO TESTS for the order-not-trade thesis:

TEST 1 — DELAYED-REPORT PRINTS
For each wick, fetch /v3/trades with the candle's 5-min participant_timestamp window
(when the dark venue says the trade happened, not when SIP saw it). Look for prints
at or beyond the wick extreme that got reported late.

TEST 2 — INSTITUTIONAL WORK-OFF AT HIT
For each wick that hit forward, find the bar where price first reached the wick
extreme (from the 10-day backtest results) and measure:
  - Volume on that hit bar vs day average
  - Dark-pool % on that hit bar (proxy via 1-min vw vs other minutes - actually pull
    trades for the 5-min hit window)
  - Compare to baseline candle dark%
"""
import os, sys, json, time
from pathlib import Path
from datetime import datetime, timedelta, timezone
import csv
import requests

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
ET = timezone(timedelta(hours=-4))
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

WICKS = json.loads(Path("data/tsla_14_wicks.json").read_text())["wicks"]

# Forward hit results from prior backtest
BACKTEST_CSV = Path("data/tsla_14_wicks_results.csv")


def fetch_trades(ticker, s_ns, e_ns, ts_field="timestamp"):
    """ts_field: 'timestamp' (participant timestamp) or 'sip_timestamp'."""
    url = f"{BASE}/v3/trades/{ticker}"
    params = {f"{ts_field}.gte": s_ns, f"{ts_field}.lt": e_ns, "limit": 50000, "order": "asc"}
    out = []
    pages = 0
    while url and pages < 25:
        r = S.get(url, params=params if pages == 0 else None, timeout=90)
        if r.status_code != 200:
            print(f"  ! HTTP {r.status_code}: {r.text[:300]}", file=sys.stderr)
            break
        j = r.json()
        out.extend(j.get("results", []))
        url = j.get("next_url")
        params = None
        pages += 1
    return out


def main():
    # ----- TEST 1 — delayed-report prints by participant_timestamp -----
    print("\n" + "=" * 90)
    print("TEST 1 — DELAYED-REPORT PRINTS (participant_timestamp window = candle 5min)")
    print("=" * 90)
    t1_rows = []
    for w in WICKS:
        direction = w["direction"]
        extreme = float(w["extreme"])
        dt_et = datetime.strptime(f"{w['date']} {w['time_et']}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
        s_ns = int(dt_et.timestamp() * 1e9)
        e_ns = int((dt_et + timedelta(minutes=5)).timestamp() * 1e9)

        # Try participant_timestamp first
        trades = fetch_trades(w["ticker"], s_ns, e_ns, ts_field="timestamp")
        # If the API doesn't honor that field, fall back; check if response looks normal
        if not trades:
            trades = fetch_trades(w["ticker"], s_ns, e_ns, ts_field="participant_timestamp")

        # Find prints at/beyond extreme (tolerance 0.05)
        if direction == "down":
            matches = [t for t in trades if (t.get("price") or 1e9) <= extreme + 0.05]
        else:
            matches = [t for t in trades if (t.get("price") or 0) >= extreme - 0.05]

        prices = [t.get("price") for t in trades if t.get("price")]
        actual_lo = min(prices) if prices else None
        actual_hi = max(prices) if prices else None

        # Look for prints where sip_timestamp is LATER than participant_timestamp by >60s
        # (delayed report). For matches near extreme, compute delay.
        delayed_at_ext = []
        for t in matches:
            ps = t.get("participant_timestamp")
            ss = t.get("sip_timestamp")
            delay_s = None
            if ps and ss:
                delay_s = (int(ss) - int(ps)) / 1e9
            if delay_s is not None and abs(delay_s) > 0:
                delayed_at_ext.append((t.get("price"), t.get("size"), delay_s, t.get("conditions"), t.get("exchange"), t.get("trf_id")))

        print(f"\n[{w['id']}] {w['date']} {w['time_et']} dir={direction} ext={extreme}  "
              f"(participant window: {len(trades)} trades, actual range {actual_lo}-{actual_hi})")
        print(f"     prints at/beyond extreme by participant_timestamp: {len(matches)}")
        print(f"     of those, with non-zero sip-vs-participant delay: {len(delayed_at_ext)}")
        for px, sz, delay, conds, exch, trf in delayed_at_ext[:5]:
            print(f"       px={px}  sz={sz}  delay={delay:.2f}s  exch={exch}  trf={trf}  conds={conds}")

        t1_rows.append({
            "id": w["id"], "date": w["date"], "time_et": w["time_et"], "direction": direction,
            "body_color": w["body_color"], "session": w["session"], "extreme": extreme,
            "participant_window_trades": len(trades),
            "participant_window_low": actual_lo, "participant_window_high": actual_hi,
            "matches_at_extreme": len(matches),
            "delayed_matches": len(delayed_at_ext),
        })
        time.sleep(0.15)

    # ----- TEST 2 — institutional work-off at hit minute -----
    print("\n" + "=" * 90)
    print("TEST 2 — INSTITUTIONAL WORK-OFF AT HIT MINUTE")
    print("=" * 90)

    # Load backtest results
    hits = {}
    if BACKTEST_CSV.exists():
        for row in csv.DictReader(open(BACKTEST_CSV)):
            hits[int(row["id"])] = row

    t2_rows = []
    for w in WICKS:
        wid = w["id"]
        h = hits.get(wid)
        if not h or h.get("hit_10d") != "True":
            print(f"\n[{wid}] no forward hit in 10d — skipping work-off test")
            t2_rows.append({"id": wid, "skipped": "no_hit"})
            continue

        hit_dt_str = h["hit_10d_dt"]  # "2026-05-11 08:03 ET"
        # parse
        try:
            hit_dt = datetime.strptime(hit_dt_str.replace(" ET", ""), "%Y-%m-%d %H:%M").replace(tzinfo=ET)
        except ValueError:
            print(f"[{wid}] couldn't parse hit dt {hit_dt_str}")
            t2_rows.append({"id": wid, "skipped": "parse_err"})
            continue

        direction = w["direction"]
        extreme = float(w["extreme"])

        # Pull 5-min window of trades around the hit minute
        s_ns = int(hit_dt.timestamp() * 1e9)
        e_ns = int((hit_dt + timedelta(minutes=5)).timestamp() * 1e9)
        trades = fetch_trades(w["ticker"], s_ns, e_ns)

        total_size = 0
        total_notional = 0.0
        dark_size = 0
        dark_notional = 0.0
        at_ext_size = 0
        at_ext_notional = 0.0
        at_ext_dark_size = 0
        largest_at_ext = 0
        largest_at_ext_print = None

        for t in trades:
            size = t.get("size") or 0
            price = t.get("price") or 0
            if size == 0 or price == 0:
                continue
            notional = size * price
            total_size += size
            total_notional += notional
            is_dark = t.get("exchange") == 4 and t.get("trf_id") is not None
            if is_dark:
                dark_size += size
                dark_notional += notional

            # "at extreme" tolerance 0.5% of extreme
            tol = extreme * 0.005
            at_ext = (direction == "down" and price <= extreme + tol) or \
                     (direction == "up"   and price >= extreme - tol)
            if at_ext:
                at_ext_size += size
                at_ext_notional += notional
                if is_dark:
                    at_ext_dark_size += size
                if size > largest_at_ext:
                    largest_at_ext = size
                    largest_at_ext_print = {
                        "price": price, "size": size, "is_dark": is_dark,
                        "exchange": t.get("exchange"), "trf_id": t.get("trf_id"),
                        "conditions": t.get("conditions") or [],
                    }

        dpr = (dark_notional / total_notional * 100) if total_notional else 0
        ext_dpr = (at_ext_dark_size / at_ext_size * 100) if at_ext_size else 0

        # Compare to candle baseline
        candle_dpr = None  # we have it in anatomy CSV; load lazily
        try:
            for r in csv.DictReader(open("data/wick_candle_anatomy.csv")):
                if int(r["id"]) == wid:
                    candle_dpr = float(r["dpr_pct"])
                    break
        except Exception:
            pass

        print(f"\n[{wid}] {w['date']} {w['time_et']} -> hit at {hit_dt_str}  ext={extreme}")
        print(f"     hit 5-min: {len(trades)} trades, total vol {total_size:,}, ${total_notional/1e6:.2f}M, DPR {dpr:.1f}%  (candle DPR {candle_dpr})")
        print(f"     at-extreme (±0.5%): size={at_ext_size:,}  ${at_ext_notional/1e6:.2f}M  ext-DPR {ext_dpr:.1f}%  largest={largest_at_ext}")
        if largest_at_ext_print:
            print(f"       largest@ext: px={largest_at_ext_print['price']}  sz={largest_at_ext_print['size']}  "
                  f"dark={largest_at_ext_print['is_dark']}  conds={largest_at_ext_print['conditions']}")

        t2_rows.append({
            "id": wid, "hit_dt": hit_dt_str,
            "hit_5m_size": total_size, "hit_5m_notional_M": round(total_notional/1e6, 2),
            "hit_5m_dpr": round(dpr, 2),
            "candle_dpr": candle_dpr,
            "at_ext_size": at_ext_size, "at_ext_notional_M": round(at_ext_notional/1e6, 2),
            "at_ext_dpr": round(ext_dpr, 2), "largest_at_ext_size": largest_at_ext,
        })
        time.sleep(0.15)

    # Write CSVs
    Path("data").mkdir(exist_ok=True)
    with open("data/wick_test1_delayed.csv", "w", newline="") as f:
        if t1_rows:
            writer = csv.DictWriter(f, fieldnames=list(t1_rows[0].keys()))
            writer.writeheader()
            writer.writerows(t1_rows)
    with open("data/wick_test2_workoff.csv", "w", newline="") as f:
        keys = set()
        for r in t2_rows:
            keys.update(r.keys())
        keys = sorted(keys)
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for r in t2_rows:
            writer.writerow(r)

    print(f"\nWrote data/wick_test1_delayed.csv and data/wick_test2_workoff.csv")


if __name__ == "__main__":
    main()
