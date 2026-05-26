"""
SPY pbar scanner v3 — January 2026, 5-min candles.

v3 FIX: Bucket trades by sip_timestamp (report time), not participant_timestamp
(happened time). ToS draws candles using report time, so late-reported Form-T
prints (cond 22) land in the bucket when they were reported, not hours back.
This prevents the same extreme price from appearing in many consecutive
candles.

v2 fix (kept): Build 5-min OHLC directly from /v3/trades (including condition
12 Form-T and 22 Form-T-late dark prints, which ToS displays but Massive's
/v2/aggs endpoint filters out).

Rule (locked):
  - 0.5% <= wick_pct < 5%   (wick_length / close * 100)
  - <=20 prints at extreme (within $0.05)
  - All sessions (04:00 - 20:00 ET), both directions

Output: data/pbar_spy_jan2026_v3.csv
"""
import os, csv, time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
ET = timezone(timedelta(hours=-5))
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

TICKER = "SPY"
START = datetime(2026, 1, 1).date()
END = datetime(2026, 1, 31).date()
WICK_MIN = 0.5
WICK_MAX = 5.0
EXT_PRINT_MAX = 20
TOL = 0.05
SESSION_START_H = 4
SESSION_END_H = 20

# Trade conditions to EXCLUDE when building ToS-style OHLC.
# We KEEP cond 12 (Form-T - real pre/post trades) and cond 22 (late Form-T,
# what ToS shows as wicks). We exclude pure odd lots, ToS hides those.
EXCLUDE_CONDS = {37}  # odd lot only
EXCLUDE_SIZE_LT = 0   # keep all sizes for now; ToS shows all-but-odd-lots

OUT = Path("data/pbar_spy_jan2026_v3.csv")
OUT.parent.mkdir(exist_ok=True, parents=True)


def fetch_trades(ticker, s_ns, e_ns):
    url = f"{BASE}/v3/trades/{ticker}"
    params = {"timestamp.gte": s_ns, "timestamp.lt": e_ns, "limit": 50000, "order": "asc"}
    out, pages = [], 0
    while url and pages < 400:
        # Retry transient network failures (ChunkedEncodingError, timeouts, 5xx)
        last_err = None
        for attempt in range(6):
            try:
                r = S.get(url, params=params if pages == 0 else None, timeout=180)
                if r.status_code == 200:
                    j = r.json()
                    out.extend(j.get("results", []))
                    url = j.get("next_url")
                    params = None
                    pages += 1
                    last_err = None
                    break
                if r.status_code >= 500 or r.status_code == 429:
                    last_err = f"HTTP {r.status_code}"
                    time.sleep(2 ** attempt)
                    continue
                print(f"  ! trades HTTP {r.status_code}: {r.text[:200]}")
                return out
            except (requests.exceptions.ChunkedEncodingError,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                last_err = str(e)[:120]
                time.sleep(2 ** attempt)
                continue
        else:
            print(f"  ! gave up after retries on page {pages}: {last_err}")
            return out
    return out


def is_tos_visible(trade):
    """Should ToS display this trade? Returns True if visible."""
    conds = set(trade.get("conditions") or [])
    if EXCLUDE_CONDS & conds:
        return False
    if (trade.get("size") or 0) < EXCLUDE_SIZE_LT:
        return False
    return True


def build_5min_candles_from_trades(trades, day_start_ms):
    """Aggregate visible trades into 5-min OHLC buckets aligned to wall clock."""
    buckets = defaultdict(list)
    for t in trades:
        if not is_tos_visible(t):
            continue
        # Use sip_timestamp (report time) - that's what ToS uses to place the
        # trade on its chart. participant_timestamp can be hours earlier for
        # late-reported Form-T prints and would smear them across candles.
        ts = t.get("sip_timestamp") or t.get("participant_timestamp")
        if not ts:
            continue
        ts_ms = int(ts) // 1_000_000  # ns -> ms
        bkt = (ts_ms // (5 * 60 * 1000)) * (5 * 60 * 1000)
        buckets[bkt].append((ts_ms, t.get("price"), t.get("size") or 0))

    candles = []
    for bkt in sorted(buckets):
        pts = sorted(buckets[bkt], key=lambda x: x[0])
        prices = [p for _, p, _ in pts if p is not None]
        if not prices:
            continue
        o = pts[0][1]
        c = pts[-1][1]
        candles.append({
            "t": bkt,
            "o": o,
            "h": max(prices),
            "l": min(prices),
            "c": c,
            "v": sum(s for _, _, s in pts),
            "n": len(pts),
        })
    return candles


def classify_wick(candle):
    o, h, l, c = candle["o"], candle["h"], candle["l"], candle["c"]
    body_top = max(o, c); body_bot = min(o, c)
    upper = h - body_top
    lower = body_bot - l
    if upper <= 0 and lower <= 0:
        return None
    if upper >= lower:
        return ("up", upper, abs(c - o), h)
    return ("down", lower, abs(c - o), l)


def session_label(dt):
    h = dt.hour + dt.minute / 60.0
    if 4 <= h < 9.5: return "pre"
    if 9.5 <= h < 16: return "rth"
    return "post"


def trading_days(s, e):
    d = s
    while d <= e:
        if d.weekday() < 5: yield d
        d += timedelta(days=1)


def main():
    rows = []
    print(f"Scanning {TICKER} {START} to {END}\n")
    for day in trading_days(START, END):
        d_start = datetime.combine(day, datetime.min.time()).replace(hour=SESSION_START_H, tzinfo=ET)
        d_end = datetime.combine(day, datetime.min.time()).replace(hour=SESSION_END_H, tzinfo=ET)
        s_ns = int(d_start.timestamp() * 1e9)
        e_ns = int(d_end.timestamp() * 1e9)
        trades = fetch_trades(TICKER, s_ns, e_ns)
        if not trades:
            print(f"  {day}: no trades (holiday?)")
            continue
        # Build 5-min candles
        candles = build_5min_candles_from_trades(trades, int(d_start.timestamp() * 1000))
        # Filter to candles inside our session window only
        kept = []
        for c in candles:
            dt = datetime.fromtimestamp(c["t"] / 1000, tz=ET)
            if dt.date() != day: continue
            if not (SESSION_START_H <= dt.hour < SESSION_END_H): continue
            kept.append((dt, c))
        print(f"  {day}: {len(trades):,} trades -> {len(kept)} 5-min candles")

        # Apply wick filter
        for dt, c in kept:
            cl = classify_wick(c)
            if not cl: continue
            direction, wick_len, body_len, extreme = cl
            wick_pct = wick_len / c["c"] * 100
            if not (WICK_MIN <= wick_pct < WICK_MAX): continue

            # Count prints at extreme in this 5-min window
            bkt_start_ns = c["t"] * 1_000_000
            bkt_end_ns = (c["t"] + 5*60*1000) * 1_000_000
            window_trades = [t for t in trades
                             if bkt_start_ns <= int(t.get("sip_timestamp") or t.get("participant_timestamp") or 0) < bkt_end_ns]
            if direction == "down":
                ext_prints = [t for t in window_trades if (t.get("price") or 1e9) <= extreme + TOL]
            else:
                ext_prints = [t for t in window_trades if (t.get("price") or 0) >= extreme - TOL]
            ext_count = len(ext_prints)
            ext_size = sum(t.get("size") or 0 for t in ext_prints)

            verdict = "PBAR" if ext_count <= EXT_PRINT_MAX else "not_pbar"

            row = {
                "date": day.isoformat(),
                "time_et": dt.strftime("%H:%M"),
                "session": session_label(dt),
                "direction": direction,
                "open": round(c["o"], 4),
                "high": round(c["h"], 4),
                "low": round(c["l"], 4),
                "close": round(c["c"], 4),
                "volume": int(c.get("v", 0)),
                "trade_count": c.get("n", 0),
                "wick_len": round(wick_len, 4),
                "body_len": round(body_len, 4),
                "wick_pct": round(wick_pct, 3),
                "extreme": round(extreme, 4),
                "prints_at_extreme": ext_count,
                "prints_at_extreme_size": ext_size,
                "verdict": verdict,
            }
            rows.append(row)
            flag = "★" if verdict == "PBAR" else " "
            print(f"  {flag} {row['date']} {row['time_et']} {direction:4} wick%={row['wick_pct']:.2f}"
                  f"  O={row['open']} H={row['high']} L={row['low']} C={row['close']}"
                  f"  ext={extreme}  ext_prints={ext_count}  -> {verdict}")

        time.sleep(0.1)

    if rows:
        with OUT.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    pbars = [r for r in rows if r["verdict"] == "PBAR"]
    print(f"\n{'='*80}\nSUMMARY: {len(rows)} candidates, {len(pbars)} PBARs\n{'='*80}")
    print(f"By direction:  up={sum(1 for r in pbars if r['direction']=='up')}  down={sum(1 for r in pbars if r['direction']=='down')}")
    print(f"By session:    pre={sum(1 for r in pbars if r['session']=='pre')}  rth={sum(1 for r in pbars if r['session']=='rth')}  post={sum(1 for r in pbars if r['session']=='post')}")
    print(f"\nAll PBARs:")
    for r in pbars:
        print(f"  {r['date']} {r['time_et']} ET  {r['direction']:4}  wick%={r['wick_pct']:.2f}  "
              f"O={r['open']} H={r['high']} L={r['low']} C={r['close']}  ext={r['extreme']}  "
              f"vol={r['volume']:,}  ext_prints={r['prints_at_extreme']}")
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
