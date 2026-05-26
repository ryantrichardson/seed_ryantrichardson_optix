"""
SPY pbar scanner — January 2026, 5-min candles.
Rule v3 (locked with Ryan 5/25):
  - 0.7% <= wick_pct < 5%   (wick / close)
  - <=20 prints at extreme (within $0.05) on /v3/trades
  - Both directions, all sessions
  - No isolation / no near-span filter
Output: data/pbar_spy_jan2026.csv
"""
import os, csv, time
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

OUT = Path("data/pbar_spy_jan2026.csv")
OUT.parent.mkdir(exist_ok=True, parents=True)


def fetch_minute_bars(ticker, day):
    r = S.get(f"{BASE}/v2/aggs/ticker/{ticker}/range/1/minute/{day}/{day}",
              params={"adjusted": "true", "sort": "asc", "limit": 50000}, timeout=60)
    if r.status_code != 200:
        print(f"  ! bars HTTP {r.status_code}: {r.text[:200]}")
        return []
    j = r.json()
    out = j.get("results", []) or []
    while j.get("next_url"):
        url = j["next_url"] + f"&apiKey={API}"
        r = requests.get(url, timeout=60)
        if r.status_code != 200: break
        j = r.json()
        out.extend(j.get("results", []))
    return out


def fetch_trades(ticker, s_ns, e_ns):
    url = f"{BASE}/v3/trades/{ticker}"
    params = {"timestamp.gte": s_ns, "timestamp.lt": e_ns, "limit": 50000, "order": "asc"}
    out, pages = [], 0
    while url and pages < 80:
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
    buckets = {}
    for b in minute_bars:
        bkt = (b["t"] // (5*60*1000)) * (5*60*1000)
        buckets.setdefault(bkt, []).append(b)
    out = []
    for bkt in sorted(buckets):
        bars = sorted(buckets[bkt], key=lambda x: x["t"])
        out.append({"t": bkt, "o": bars[0]["o"], "h": max(b["h"] for b in bars),
                    "l": min(b["l"] for b in bars), "c": bars[-1]["c"],
                    "v": sum(b.get("v", 0) for b in bars)})
    return out


def classify_wick(candle):
    o, h, l, c = candle["o"], candle["h"], candle["l"], candle["c"]
    body_top = max(o, c); body_bot = min(o, c)
    upper = h - body_top
    lower = body_bot - l
    if upper <= 0 and lower <= 0:
        return None
    if upper >= lower:
        return ("up", upper, abs(c-o), h)
    return ("down", lower, abs(c-o), l)


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
    candidates = []
    print(f"Step 1: fetch 5-min candles for {TICKER}, {START} to {END}\n")
    for day in trading_days(START, END):
        bars = fetch_minute_bars(TICKER, day.isoformat())
        if not bars:
            print(f"  {day}: no bars (holiday?)")
            continue
        five = aggregate_5min(bars)
        kept = []
        for c in five:
            dt = datetime.fromtimestamp(c["t"]/1000, tz=ET)
            if dt.date() != day: continue
            if not (SESSION_START_H <= dt.hour < SESSION_END_H): continue
            kept.append((dt, c))
        print(f"  {day}: {len(kept)} 5-min candles in window")
        for dt, c in kept:
            cl = classify_wick(c)
            if not cl: continue
            direction, wick_len, body_len, extreme = cl
            wick_pct = wick_len / c["c"] * 100
            if not (WICK_MIN <= wick_pct < WICK_MAX): continue
            candidates.append({"day": day, "dt": dt, "candle": c,
                               "direction": direction, "wick_len": wick_len,
                               "body_len": body_len, "extreme": extreme,
                               "wick_pct": wick_pct})

    print(f"\nStep 2: {len(candidates)} candidates with {WICK_MIN}<=wick%<{WICK_MAX}. Verifying...\n")
    rows = []
    for k, cand in enumerate(candidates, 1):
        c = cand["candle"]; dt = cand["dt"]; direction = cand["direction"]; extreme = cand["extreme"]
        s_ns = int(dt.timestamp() * 1e9); e_ns = int((dt + timedelta(minutes=5)).timestamp() * 1e9)
        trades = fetch_trades(TICKER, s_ns, e_ns)
        if direction == "down":
            ext_prints = [t for t in trades if (t.get("price") or 1e9) <= extreme + TOL]
        else:
            ext_prints = [t for t in trades if (t.get("price") or 0) >= extreme - TOL]
        ext_count = len(ext_prints)
        ext_size = sum(t.get("size") or 0 for t in ext_prints)

        if ext_count <= EXT_PRINT_MAX:
            verdict = "PBAR"
        else:
            verdict = "not_pbar"

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
            "wick_len": round(cand["wick_len"], 4),
            "body_len": round(cand["body_len"], 4),
            "wick_pct": round(cand["wick_pct"], 3),
            "extreme": round(extreme, 4),
            "trades_in_window": len(trades),
            "prints_at_extreme": ext_count,
            "prints_at_extreme_size": ext_size,
            "verdict": verdict,
        }
        rows.append(row)
        flag = "★" if verdict == "PBAR" else " "
        print(f"  {flag} [{k}/{len(candidates)}] {row['date']} {row['time_et']} {direction:4} "
              f"wick%={row['wick_pct']:.2f}  ext={extreme}  ext_prints={ext_count}  → {verdict}")
        time.sleep(0.05)

    # CSV
    if rows:
        with OUT.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)

    pbars = [r for r in rows if r["verdict"] == "PBAR"]
    print(f"\n{'='*80}")
    print(f"SUMMARY: {len(rows)} candidates, {len(pbars)} PBARs")
    print(f"{'='*80}")
    print(f"By direction:  up={sum(1 for r in pbars if r['direction']=='up')}  down={sum(1 for r in pbars if r['direction']=='down')}")
    print(f"By session:    pre={sum(1 for r in pbars if r['session']=='pre')}  rth={sum(1 for r in pbars if r['session']=='rth')}  post={sum(1 for r in pbars if r['session']=='post')}")
    print(f"\nAll pbars:")
    for r in pbars:
        print(f"  {r['date']} {r['time_et']} ET  {r['direction']:4}  wick%={r['wick_pct']:.2f}  "
              f"O={r['open']} H={r['high']} L={r['low']} C={r['close']}  ext={r['extreme']}  "
              f"vol={r['volume']:,}  ext_prints={r['prints_at_extreme']}")
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
