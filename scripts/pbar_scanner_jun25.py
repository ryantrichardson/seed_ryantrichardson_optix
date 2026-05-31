"""PBAR scanner for the Jun 2025 - May 2026 slim_trades artifacts.
Reads /tmp/artifacts/{spy,qqq}_jun25_shard*/slim_trades_*_jun25_shard*.csv.gz.
Writes data/pbar_results/pbar_{ticker}_jun25_may26.csv to repo.

Parameterized via TICKER env var (SPY or QQQ).
Same classification logic as scripts/pbar_scanner_extension.py.
"""
import os, csv, gzip, json, time, pickle, glob
from collections import defaultdict
from datetime import datetime, date
from zoneinfo import ZoneInfo
from pathlib import Path

ET = ZoneInfo("America/New_York")

TICKER = os.environ.get("TICKER", "SPY")
WICK_MIN = 0.5
WICK_MAX = 5.0
EXT_PRINT_MAX = 20
TOL = 0.05
SESSION_START_H = 4
SESSION_END_H = 20
EXCLUDE_CONDS = {37, 2, 52}

# Inputs: 8 shard csv.gz files for this ticker
ARTIFACT_GLOB = f"/tmp/artifacts/{TICKER.lower()}_jun25_shard*/slim_trades_{TICKER.lower()}_jun25_shard*.csv.gz"
SHARDS = sorted(glob.glob(ARTIFACT_GLOB))
print(f"TICKER = {TICKER}")
print(f"Glob   = {ARTIFACT_GLOB}")
print(f"Found {len(SHARDS)} shards:")
for s in SHARDS: print(f"  {s}")

OUT = Path(f"data/pbar_results/pbar_{TICKER.lower()}_jun25_may26.csv")
OUT.parent.mkdir(exist_ok=True, parents=True)
PKL_DIR = Path(f"/tmp/per_day_{TICKER.lower()}_jun25")
PKL_DIR.mkdir(exist_ok=True)


def parse_conditions(s):
    if not s or s in ("[]", "None"): return frozenset()
    try:
        if s.startswith("["):
            return frozenset(json.loads(s))
        return frozenset(int(x) for x in s.strip().strip("[]").split(",") if x.strip())
    except Exception:
        return frozenset()


def parse_int(s, default=0):
    if not s or s == "None": return default
    try: return int(s)
    except (ValueError, TypeError):
        try: return int(float(s))
        except Exception: return default


def parse_float(s):
    if not s or s == "None": return None
    try: return float(s)
    except (ValueError, TypeError): return None


def phase1():
    print(f"=== Phase 1: partition {len(SHARDS)} shards by day ===", flush=True)
    for shard in SHARDS:
        t0 = time.time()
        print(f"  Reading {shard}...", flush=True)
        by_day = defaultdict(list)
        rows_kept = 0
        try:
            with gzip.open(shard, "rt", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("ticker") != TICKER: continue
                    price = parse_float(row.get("price"))
                    sip = parse_int(row.get("sip_timestamp"))
                    if price is None or sip == 0: continue
                    conds = parse_conditions(row.get("conditions"))
                    trf = row.get("trf_id") not in ("", "None", None)
                    by_day[row["trade_date"]].append((
                        price,
                        parse_int(row.get("size")),
                        sip,
                        parse_int(row.get("participant_timestamp")),
                        trf,
                        conds,
                    ))
                    rows_kept += 1
        except EOFError:
            print(f"    EOF (partial gzip ok), kept {rows_kept:,} so far", flush=True)
        except Exception as e:
            print(f"    Error: {e}; kept {rows_kept:,} so far", flush=True)

        for d, items in by_day.items():
            pf = PKL_DIR / f"{d}.pkl"
            with pf.open("ab") as out:
                pickle.dump(items, out)
        print(f"    {rows_kept:,} rows kept, {len(by_day)} dates touched in {time.time()-t0:.0f}s", flush=True)
        del by_day

    dates = sorted(p.stem for p in PKL_DIR.glob("*.pkl"))
    print(f"Phase 1 done. {len(dates)} per-day files.")
    return dates


def load_day(d):
    trades = []
    with (PKL_DIR / f"{d}.pkl").open("rb") as f:
        while True:
            try:
                chunk = pickle.load(f)
            except EOFError:
                break
            trades.extend(chunk)
    return trades


def build_5min(trades):
    buckets = defaultdict(list)
    for tr in trades:
        price, size, sip, par, trf, conds = tr
        if EXCLUDE_CONDS & conds: continue
        ts_ms = sip // 1_000_000
        bkt = (ts_ms // (5 * 60 * 1000)) * (5 * 60 * 1000)
        buckets[bkt].append((ts_ms, price, size))
    out = []
    for bkt in sorted(buckets):
        pts = sorted(buckets[bkt], key=lambda x: x[0])
        prices = [p for _, p, _ in pts]
        out.append({"t": bkt, "o": pts[0][1], "h": max(prices), "l": min(prices),
                    "c": pts[-1][1], "v": sum(s for _, _, s in pts), "n": len(pts)})
    return out


def classify_wick(c):
    o, h, l, cl = c["o"], c["h"], c["l"], c["c"]
    body_top, body_bot = max(o, cl), min(o, cl)
    upper, lower = h - body_top, body_bot - l
    if upper <= 0 and lower <= 0: return None
    if upper >= lower: return ("up", upper, abs(cl - o), h)
    return ("down", lower, abs(cl - o), l)


def session_label(dt):
    h = dt.hour + dt.minute / 60.0
    if 4 <= h < 9.5: return "pre"
    if 9.5 <= h < 16: return "rth"
    return "post"


def analyze_day(day_str, trades):
    day = date.fromisoformat(day_str)
    candles = build_5min(trades)
    kept = [(datetime.fromtimestamp(c["t"]/1000, tz=ET), c) for c in candles]
    kept = [(dt, c) for dt, c in kept
            if dt.date() == day and SESSION_START_H <= dt.hour < SESSION_END_H]

    rows = []
    priors = []
    for dt, c in kept:
        sess = session_label(dt)
        cl = classify_wick(c)
        if not cl:
            priors.append({"session": sess, "h": c["h"], "l": c["l"]})
            continue
        direction, wick_len, body_len, extreme = cl
        wick_pct = wick_len / c["c"] * 100
        if not (WICK_MIN <= wick_pct < WICK_MAX):
            priors.append({"session": sess, "h": c["h"], "l": c["l"]})
            continue

        bkt_start_ns = c["t"] * 1_000_000
        bkt_end_ns = (c["t"] + 5 * 60 * 1000) * 1_000_000
        if direction == "down":
            ext = [tr for tr in trades if bkt_start_ns <= tr[2] < bkt_end_ns and tr[0] <= extreme + TOL]
        else:
            ext = [tr for tr in trades if bkt_start_ns <= tr[2] < bkt_end_ns and tr[0] >= extreme - TOL]
        ext_count = len(ext)
        ext_size = sum(tr[1] for tr in ext)

        all_dark = bool(ext) and all(tr[4] for tr in ext)
        has_ghost_cond = any(tr[5] & {7, 22, 32, 53} for tr in ext)
        max_lag_ms = 0
        for tr in ext:
            sip, par = tr[2], tr[3]
            if sip and par:
                lag = (sip - par) / 1_000_000
                if lag > max_lag_ms: max_lag_ms = lag

        if ext_count > EXT_PRINT_MAX:
            verdict = "not_pbar"
        elif all_dark and (has_ghost_cond or max_lag_ms > 5000):
            verdict = "GHOST"
        else:
            verdict = "PBAR"

        repeat = any(p["session"] == sess and p["l"] - TOL <= extreme <= p["h"] + TOL for p in priors)

        rows.append({
            "date": day.isoformat(), "time_et": dt.strftime("%H:%M"), "session": sess,
            "direction": direction,
            "open": round(c["o"], 4), "high": round(c["h"], 4),
            "low": round(c["l"], 4), "close": round(c["c"], 4),
            "volume": int(c["v"]), "trade_count": c["n"],
            "wick_len": round(wick_len, 4), "body_len": round(body_len, 4),
            "wick_pct": round(wick_pct, 3), "extreme": round(extreme, 4),
            "prints_at_extreme": ext_count, "prints_at_extreme_size": ext_size,
            "max_extreme_lag_ms": int(max_lag_ms),
            "all_dark": all_dark, "repeat_level": repeat,
            "verdict": verdict,
        })
        priors.append({"session": sess, "h": c["h"], "l": c["l"]})
    return rows


def main():
    dates = phase1()
    print(f"\n=== Phase 2: analyze {len(dates)} days ===", flush=True)
    all_rows = []
    with OUT.open("w", newline="") as fout:
        writer = None
        for i, d in enumerate(dates, 1):
            t0 = time.time()
            trades = load_day(d)
            rows = analyze_day(d, trades)
            pc = sum(1 for r in rows if r["verdict"] == "PBAR")
            gc = sum(1 for r in rows if r["verdict"] == "GHOST")
            print(f"  [{i}/{len(dates)}] {d}: {len(trades):,} trades, {len(rows)} cands, "
                  f"{pc} PBAR, {gc} GHOST  ({time.time()-t0:.1f}s)", flush=True)
            if rows and writer is None:
                writer = csv.DictWriter(fout, fieldnames=list(rows[0].keys()))
                writer.writeheader()
            if writer and rows:
                writer.writerows(rows); fout.flush()
            all_rows.extend(rows)
            del trades

    pbars = [r for r in all_rows if r["verdict"] == "PBAR"]
    ghosts = [r for r in all_rows if r["verdict"] == "GHOST"]
    print(f"\nSUMMARY {TICKER}: {len(all_rows)} cands, {len(pbars)} PBARs, {len(ghosts)} GHOSTs")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
