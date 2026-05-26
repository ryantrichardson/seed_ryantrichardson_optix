"""
QQQ pbar (phantom bar / ghost bar) scanner — January 2026, 5-min candles.

A 5-min candle is a 'pbar' if:
  1. 1.0 <= wick_pct < 5.0   where wick_pct = wick_length / close * 100
  2. The wick extreme price has ZERO prints at/beyond it in /v3/trades
     during the candle's 5-min window (tolerance $0.05).
  3. Isolation: no candle in surrounding +/- 10 min (i.e. the four
     5-min candles before and the four 5-min candles after) traded
     within 50% of the wick distance.

All sessions included: 04:00 - 20:00 ET.
Both up-wicks and down-wicks evaluated.

Output: data/pbar_qqq_jan2026.csv
"""

import os, csv, json, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
ET = timezone(timedelta(hours=-5))  # EST in January
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

TICKER = "QQQ"
START = datetime(2026, 1, 1).date()
END = datetime(2026, 1, 31).date()
WICK_MIN = 1.0
WICK_MAX = 5.0
TOL = 0.05  # $ tolerance when checking "at extreme"
SESSION_START_H = 4   # 04:00 ET
SESSION_END_H = 20    # 20:00 ET

OUT = Path("data/pbar_qqq_jan2026.csv")
OUT.parent.mkdir(exist_ok=True, parents=True)


def fetch_minute_bars(ticker, day):
    """Fetch all 1-min bars for one day (extended hours included)."""
    r = S.get(
        f"{BASE}/v2/aggs/ticker/{ticker}/range/1/minute/{day}/{day}",
        params={"adjusted": "true", "sort": "asc", "limit": 50000},
        timeout=60,
    )
    if r.status_code != 200:
        print(f"  ! bars HTTP {r.status_code}: {r.text[:200]}")
        return []
    j = r.json()
    out = j.get("results", []) or []
    while j.get("next_url"):
        url = j["next_url"] + f"&apiKey={API}"
        r = requests.get(url, timeout=60)
        if r.status_code != 200:
            break
        j = r.json()
        out.extend(j.get("results", []))
    return out


def fetch_trades(ticker, s_ns, e_ns):
    url = f"{BASE}/v3/trades/{ticker}"
    params = {"timestamp.gte": s_ns, "timestamp.lt": e_ns, "limit": 50000, "order": "asc"}
    out, pages = [], 0
    while url and pages < 50:
        r = S.get(url, params=params if pages == 0 else None, timeout=90)
        if r.status_code != 200:
            print(f"  ! trades HTTP {r.status_code}: {r.text[:200]}")
            break
        j = r.json()
        out.extend(j.get("results", []))
        url = j.get("next_url")
        params = None
        pages += 1
    return out


def aggregate_5min(minute_bars):
    """Aggregate 1-min bars to 5-min candles aligned to wall-clock :00 :05 :10 ..."""
    buckets = {}
    for b in minute_bars:
        ts_ms = b["t"]
        bucket_ms = (ts_ms // (5 * 60 * 1000)) * (5 * 60 * 1000)
        d = buckets.setdefault(bucket_ms, {"t": bucket_ms, "bars": []})
        d["bars"].append(b)
    out = []
    for bucket_ms in sorted(buckets):
        bars = buckets[bucket_ms]["bars"]
        bars.sort(key=lambda x: x["t"])
        out.append({
            "t": bucket_ms,
            "o": bars[0]["o"],
            "h": max(b["h"] for b in bars),
            "l": min(b["l"] for b in bars),
            "c": bars[-1]["c"],
            "v": sum(b.get("v", 0) for b in bars),
        })
    return out


def classify(candle):
    """Return (direction, wick_len, body_len, extreme) for a 5-min candle.
    direction = 'down' (lower wick) or 'up' (upper wick) - we evaluate the
    LONGER wick. Returns None if no wick.
    """
    o, h, l, c = candle["o"], candle["h"], candle["l"], candle["c"]
    body_top = max(o, c)
    body_bot = min(o, c)
    upper = h - body_top
    lower = body_bot - l
    if upper <= 0 and lower <= 0:
        return None
    if upper >= lower:
        return ("up", upper, abs(c - o), h)
    return ("down", lower, abs(c - o), l)


def session_label(dt_et):
    h = dt_et.hour + dt_et.minute / 60.0
    if 4 <= h < 9.5:
        return "pre"
    if 9.5 <= h < 16:
        return "rth"
    return "post"


def trading_days(start, end):
    """Yield weekdays between start and end inclusive (we'll skip empty days)."""
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def main():
    all_candles_by_day = {}
    candidates = []  # candles that pass the 1-5% wick filter

    print(f"Step 1: fetch 5-min candles for {TICKER}, {START} to {END}\n")
    for day in trading_days(START, END):
        bars = fetch_minute_bars(TICKER, day.isoformat())
        if not bars:
            print(f"  {day}: no bars (holiday?)")
            continue
        five = aggregate_5min(bars)
        # Keep only candles in extended-hours session window
        kept = []
        for c in five:
            dt = datetime.fromtimestamp(c["t"] / 1000, tz=ET)
            if dt.date() != day:
                continue
            if not (SESSION_START_H <= dt.hour < SESSION_END_H):
                continue
            kept.append(c)
        all_candles_by_day[day] = kept
        print(f"  {day}: {len(kept)} 5-min candles in window")

        for idx, c in enumerate(kept):
            cl = classify(c)
            if not cl:
                continue
            direction, wick_len, body_len, extreme = cl
            wick_pct = wick_len / c["c"] * 100
            if not (WICK_MIN <= wick_pct < WICK_MAX):
                continue
            dt = datetime.fromtimestamp(c["t"] / 1000, tz=ET)
            candidates.append({
                "day": day,
                "idx_in_day": idx,
                "dt_et": dt,
                "candle": c,
                "direction": direction,
                "wick_len": wick_len,
                "body_len": body_len,
                "extreme": extreme,
                "wick_pct": wick_pct,
            })

    print(f"\nStep 2: {len(candidates)} candidates with 1<=wick%<5. Verifying each...\n")

    rows = []
    for k, cand in enumerate(candidates, 1):
        c = cand["candle"]
        dt = cand["dt_et"]
        direction = cand["direction"]
        extreme = cand["extreme"]
        wick_len = cand["wick_len"]
        body_top = max(c["o"], c["c"])
        body_bot = min(c["o"], c["c"])

        # Isolation check (in-process, no API): look at the 4 candles before and 4 after.
        day_candles = all_candles_by_day[cand["day"]]
        idx = cand["idx_in_day"]
        neighbors = day_candles[max(0, idx - 2): idx] + day_candles[idx + 1: idx + 3]
        # +/-10 min = +/- 2 five-min candles on each side
        threshold = wick_len * 0.5
        isolation_ok = True
        violator = None
        for n in neighbors:
            if direction == "down":
                # Does this neighbor trade within 50% of the wick distance? i.e. does
                # neighbor low go below body_bot - threshold ?
                if n["l"] <= body_bot - threshold:
                    isolation_ok = False
                    violator = n
                    break
            else:
                if n["h"] >= body_top + threshold:
                    isolation_ok = False
                    violator = n
                    break

        # Trade-tape verification
        s_ns = int(dt.timestamp() * 1e9)
        e_ns = int((dt + timedelta(minutes=5)).timestamp() * 1e9)
        trades = fetch_trades(TICKER, s_ns, e_ns)
        if direction == "down":
            ext_prints = [t for t in trades if (t.get("price") or 1e9) <= extreme + TOL]
        else:
            ext_prints = [t for t in trades if (t.get("price") or 0) >= extreme - TOL]
        ext_count = len(ext_prints)
        tape_ghost = (ext_count == 0)

        verdict = "PBAR" if (tape_ghost and isolation_ok) else "not_pbar"
        if not tape_ghost:
            reason = "prints_at_extreme"
        elif not isolation_ok:
            reason = "isolation_fail"
        else:
            reason = "passes_all"

        row = {
            "date": cand["day"].isoformat(),
            "time_et": dt.strftime("%H:%M"),
            "session": session_label(dt),
            "direction": direction,
            "open": round(c["o"], 4),
            "high": round(c["h"], 4),
            "low": round(c["l"], 4),
            "close": round(c["c"], 4),
            "volume": int(c.get("v", 0)),
            "wick_len": round(wick_len, 4),
            "body_len": round(cand["body_len"], 4),
            "wick_pct": round(cand["wick_pct"], 3),
            "extreme": round(extreme, 4),
            "trades_in_window": len(trades),
            "prints_at_extreme": ext_count,
            "isolation_ok": isolation_ok,
            "verdict": verdict,
            "fail_reason": reason,
        }
        rows.append(row)
        flag = "★" if verdict == "PBAR" else " "
        print(f"  {flag} [{k}/{len(candidates)}] {row['date']} {row['time_et']} {direction:4} "
              f"wick_pct={row['wick_pct']:.2f}%  ext={extreme}  prints_at_ext={ext_count}  iso={isolation_ok}  → {verdict}")

        time.sleep(0.05)

    # Write CSV
    if rows:
        with OUT.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    # Summary
    pbars = [r for r in rows if r["verdict"] == "PBAR"]
    print(f"\n{'='*80}")
    print(f"SUMMARY: {len(rows)} candidates evaluated, {len(pbars)} pbars found")
    print(f"{'='*80}")
    print(f"By direction:  up={sum(1 for r in pbars if r['direction']=='up')}  down={sum(1 for r in pbars if r['direction']=='down')}")
    print(f"By session:    pre={sum(1 for r in pbars if r['session']=='pre')}  rth={sum(1 for r in pbars if r['session']=='rth')}  post={sum(1 for r in pbars if r['session']=='post')}")
    print(f"\nAll pbars:")
    for r in pbars:
        print(f"  {r['date']} {r['time_et']} ET  {r['direction']:4}  wick%={r['wick_pct']:.2f}  "
              f"O={r['open']} H={r['high']} L={r['low']} C={r['close']}  ext={r['extreme']}  vol={r['volume']:,}")

    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
