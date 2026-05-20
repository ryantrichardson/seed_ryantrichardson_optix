"""
Ghost wick backtester v2 — properly defined.

A "ghost wick" candle satisfies ALL of:
1. The wick (high - max(o,c) for upper, or min(o,c) - low for lower) is
   AT LEAST K times the body (|o - c|), where K is tested at 3, 5, 10
2. The wick extreme is ISOLATED: no candle in the surrounding 10 minutes
   (5 before, 5 after) has a high (for upper wicks) or low (for lower wicks)
   within 50% of the wick distance from the body
3. The body itself is small: |o - c| < 0.5% of price
4. The wick is meaningful: wick distance >= 0.5% of price

Forward test:
- For each ghost wick, look forward starting from the NEXT minute on the SAME DAY,
  through up to 10 trading days afterward
- "Hit" = price touched the wick extreme at any point in that window
- Same-day touches BEFORE the wick timestamp are NOT counted

Two parallel scans:
- SIP version: Use Polygon/Massive's 1-min aggregate OHLC
- TRADE version: Build minute bars locally from /v3/trades, INCLUDING TRF prints
  (this matches what ToS shows)
"""
import os, requests, time, csv, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

TICKER = os.environ.get("TICKER", "TSLA")
START = os.environ.get("START_DATE", "2025-11-19")
END   = os.environ.get("END_DATE",   "2026-05-19")
LOOKFORWARD = 10  # trading days
ET = timezone(timedelta(hours=-4))

print(f"=== Ghost Wick Backtest v2 ===")
print(f"  Ticker: {TICKER}")
print(f"  Range: {START} to {END}")
print(f"  Lookforward: {LOOKFORWARD} days")
print()

# Get trading days
r = S.get(f"{BASE}/v2/aggs/ticker/{TICKER}/range/1/day/{START}/2026-12-31",
          params={"limit": 5000}, timeout=30)
all_days = r.json().get("results", [])
day_bars = {}
trading_days = []
for d in all_days:
    dt = datetime.fromtimestamp(d['t']/1000, tz=timezone.utc).date()
    day_bars[dt] = {"o": d['o'], "h": d['h'], "l": d['l'], "c": d['c'], "v": d['v']}
    trading_days.append(dt)
trading_days.sort()
day_index = {d: i for i, d in enumerate(trading_days)}
print(f"Trading days in lookforward universe: {len(trading_days)}")

# Filter to backtest window
scan_days = [d for d in trading_days if START <= d.strftime("%Y-%m-%d") <= END]
print(f"Days to scan: {len(scan_days)}")
print()

def detect_wicks(bars, build_method="sip"):
    """
    bars: list of dicts with keys: t (datetime ET), o, h, l, c, v
    Returns list of ghost wicks.
    """
    wicks = []
    for i, b in enumerate(bars):
        if i < 5 or i >= len(bars) - 5:
            continue  # need neighbors on both sides
        body = abs(b['o'] - b['c'])
        upper_wick = b['h'] - max(b['o'], b['c'])
        lower_wick = min(b['o'], b['c']) - b['l']
        price = (b['o'] + b['c']) / 2
        if price <= 0:
            continue
        body_pct = body / price * 100

        # Body must be small (<0.5%)
        if body_pct >= 0.5:
            continue

        for direction, wick in [("up", upper_wick), ("down", lower_wick)]:
            if wick <= 0:
                continue
            wick_pct = wick / price * 100
            # Wick must be meaningful (>=0.5%)
            if wick_pct < 0.5:
                continue
            # Wick must be much larger than body — compute ratio
            ratio = wick / max(body, 0.0001)
            if ratio < 3.0:
                continue

            # Isolation check: ±5 bars on each side
            extreme = b['h'] if direction == "up" else b['l']
            body_top = max(b['o'], b['c'])
            body_bot = min(b['o'], b['c'])
            half_depth = wick / 2

            isolated = True
            for j in range(i-5, i+6):
                if j == i or j < 0 or j >= len(bars):
                    continue
                nb = bars[j]
                if direction == "up":
                    # Did any neighbor's high go more than half-way toward our extreme?
                    threshold = body_top + half_depth
                    if nb['h'] >= threshold:
                        isolated = False
                        break
                else:
                    threshold = body_bot - half_depth
                    if nb['l'] <= threshold:
                        isolated = False
                        break
            if not isolated:
                continue

            wicks.append({
                "method": build_method,
                "datetime": b['t'].isoformat(),
                "date": b['t'].date().isoformat(),
                "time_et": b['t'].strftime("%H:%M"),
                "open": round(b['o'], 4),
                "high": round(b['h'], 4),
                "low": round(b['l'], 4),
                "close": round(b['c'], 4),
                "volume": int(b['v']),
                "body_pct": round(body_pct, 4),
                "wick_pct": round(wick_pct, 4),
                "ratio": round(ratio, 2),
                "direction": direction,
                "extreme": round(extreme, 4),
            })
    return wicks

def score(wicks, intraday_bars_by_date):
    """For each wick, check if price touched the extreme within LOOKFORWARD trading days."""
    for w in wicks:
        d = datetime.strptime(w["date"], "%Y-%m-%d").date()
        if d not in day_index:
            w["touched"] = None
            w["days_to_touch"] = None
            continue
        target = w["extreme"]
        direction = w["direction"]
        wick_dt = datetime.fromisoformat(w["datetime"])

        touched_offset = None
        touch_dt = None

        # Same-day check: any bar AFTER wick_dt on same day where price touched extreme
        same_day_bars = intraday_bars_by_date.get(d, [])
        for nb in same_day_bars:
            if nb['t'] <= wick_dt:
                continue
            if direction == "down" and nb['l'] <= target:
                touched_offset = 0
                touch_dt = nb['t']
                break
            elif direction == "up" and nb['h'] >= target:
                touched_offset = 0
                touch_dt = nb['t']
                break

        # If not touched same-day, check forward days
        if touched_offset is None:
            start_i = day_index[d]
            for offset in range(1, LOOKFORWARD + 1):
                if start_i + offset >= len(trading_days):
                    break
                fwd = trading_days[start_i + offset]
                bar = day_bars.get(fwd)
                if not bar:
                    continue
                if direction == "down" and bar["l"] <= target:
                    touched_offset = offset
                    break
                elif direction == "up" and bar["h"] >= target:
                    touched_offset = offset
                    break

        w["touched"] = touched_offset is not None
        w["days_to_touch"] = touched_offset

all_wicks_sip = []
all_wicks_trade = []
intraday_bars_sip_by_date = {}
intraday_bars_trade_by_date = {}

for day_idx, day in enumerate(scan_days):
    day_str = day.strftime("%Y-%m-%d")

    # === SIP minute bars ===
    r = S.get(f"{BASE}/v2/aggs/ticker/{TICKER}/range/1/minute/{day_str}/{day_str}",
              params={"limit": 50000}, timeout=30)
    raw_mins = r.json().get("results", [])
    sip_bars = []
    for m in raw_mins:
        t = datetime.fromtimestamp(m['t']/1000, tz=timezone.utc).astimezone(ET)
        if 9 <= t.hour < 16 or (t.hour == 9 and t.minute >= 30):
            sip_bars.append({"t": t, "o": m['o'], "h": m['h'], "l": m['l'], "c": m['c'], "v": m['v']})
    # Filter to regular hours
    sip_bars = [b for b in sip_bars if (b['t'].hour == 9 and b['t'].minute >= 30)
                                       or (10 <= b['t'].hour < 16)]
    intraday_bars_sip_by_date[day] = sip_bars
    sip_wicks = detect_wicks(sip_bars, "sip")
    all_wicks_sip.extend(sip_wicks)

    # === Trade-level minute bars (build locally, include TRF prints) ===
    start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=ET)
    end   = datetime(day.year, day.month, day.day, 16, 0, tzinfo=ET)
    start_ns = int(start.timestamp() * 1e9)
    end_ns   = int(end.timestamp() * 1e9)
    u = f"{BASE}/v3/trades/{TICKER}"
    p = {"timestamp.gte": start_ns, "timestamp.lt": end_ns, "limit": 50000, "order": "asc"}
    by_minute = defaultdict(list)
    pages = 0
    while u and pages < 200:
        for attempt in range(5):
            try:
                r = S.get(u, params=p if pages == 0 else None, timeout=120); break
            except Exception:
                time.sleep(1 + attempt)
        if r.status_code != 200:
            break
        j = r.json()
        for t in j.get("results", []):
            ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
            if not ts_ns:
                continue
            ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(ET)
            minute = ts.replace(second=0, microsecond=0)
            # Skip conditions known to be VWAP/late/auction
            conds = set(t.get("conditions") or [])
            if conds & {2, 12, 16, 33, 52, 53}:
                # Still include for SIP-style if not TRF, but skip for trade bars too
                continue
            by_minute[minute].append({"price": t["price"], "size": t.get("size", 0), "ts": ts})
        u = j.get("next_url"); p = None; pages += 1

    trade_bars = []
    for minute in sorted(by_minute):
        trades = by_minute[minute]
        if not trades:
            continue
        trades_sorted = sorted(trades, key=lambda x: x["ts"])
        prices = [tr["price"] for tr in trades_sorted]
        vol = sum(tr["size"] for tr in trades_sorted)
        trade_bars.append({
            "t": minute, "o": prices[0], "h": max(prices), "l": min(prices),
            "c": prices[-1], "v": vol
        })
    intraday_bars_trade_by_date[day] = trade_bars
    trade_wicks = detect_wicks(trade_bars, "trade")
    all_wicks_trade.extend(trade_wicks)

    if day_idx % 10 == 0:
        print(f"  {day_str}: SIP bars={len(sip_bars)} wicks={len(sip_wicks)}, "
              f"TRADE bars={len(trade_bars)} wicks={len(trade_wicks)}")

print(f"\nTotal SIP wicks: {len(all_wicks_sip)}")
print(f"Total TRADE wicks: {len(all_wicks_trade)}")

# Score
print("\nScoring SIP wicks...")
score(all_wicks_sip, intraday_bars_sip_by_date)
print("Scoring TRADE wicks...")
score(all_wicks_trade, intraday_bars_trade_by_date)

# Save CSVs
os.makedirs("data", exist_ok=True)
for name, ws in [("sip", all_wicks_sip), ("trade", all_wicks_trade)]:
    fp = f"data/ghost_wicks_v2_{TICKER}_{name}.csv"
    if ws:
        with open(fp, "w", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=list(ws[0].keys()))
            wr.writeheader()
            wr.writerows(ws)
    print(f"Wrote {fp}")

# Summary
def summarize(name, ws):
    print(f"\n=== {name.upper()} SUMMARY ===")
    print(f"Total wicks: {len(ws)}")
    # Only score wicks with >=10 forward days
    cutoff = trading_days[-LOOKFORWARD].isoformat() if len(trading_days) > LOOKFORWARD else END
    scoreable = [w for w in ws if w["date"] <= cutoff]
    print(f"Scoreable (with {LOOKFORWARD} forward days): {len(scoreable)}")
    if not scoreable:
        return
    touched = [w for w in scoreable if w["touched"]]
    print(f"Touched: {len(touched)} ({100*len(touched)/len(scoreable):.1f}%)")
    if touched:
        avg = sum(w["days_to_touch"] for w in touched) / len(touched)
        print(f"Avg days to touch: {avg:.2f}")
        same_day = sum(1 for w in touched if w["days_to_touch"] == 0)
        print(f"  Same-day touches (after wick): {same_day}")
        print(f"  Next-day or later: {len(touched) - same_day}")

    # By ratio tier
    print(f"\nBy wick/body ratio:")
    for lo, hi in [(3, 5), (5, 10), (10, 20), (20, 1000)]:
        sub = [w for w in scoreable if lo <= w["ratio"] < hi]
        sub_t = [w for w in sub if w["touched"]]
        if sub:
            print(f"  ratio {lo}-{hi}x: {len(sub)} total, {len(sub_t)} touched ({100*len(sub_t)/len(sub):.1f}%)")

    print(f"\nBy wick % of price:")
    for lo, hi in [(0.5, 1), (1, 2), (2, 5), (5, 100)]:
        sub = [w for w in scoreable if lo <= w["wick_pct"] < hi]
        sub_t = [w for w in sub if w["touched"]]
        if sub:
            print(f"  {lo}-{hi}%: {len(sub)} total, {len(sub_t)} touched ({100*len(sub_t)/len(sub):.1f}%)")

    print(f"\nBy direction:")
    for direction in ["up", "down"]:
        sub = [w for w in scoreable if w["direction"] == direction]
        sub_t = [w for w in sub if w["touched"]]
        if sub:
            print(f"  {direction}: {len(sub)} total, {len(sub_t)} touched ({100*len(sub_t)/len(sub):.1f}%)")

    # Top 10 most extreme
    print(f"\nTop 10 most extreme wicks (by wick%):")
    top = sorted(scoreable, key=lambda x: -x["wick_pct"])[:10]
    for w in top:
        print(f"  {w['date']} {w['time_et']} {w['direction']} wick {w['wick_pct']}% "
              f"ratio={w['ratio']}x extreme=${w['extreme']} "
              f"-> {'TOUCH d'+str(w['days_to_touch']) if w['touched'] else 'NO'}")

summarize("sip", all_wicks_sip)
summarize("trade", all_wicks_trade)
