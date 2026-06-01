"""Build per-day 1-minute OHLC bars from slim_trades_*_jun25_shard*.csv.gz artifacts.
Output: data/minute_bars/{ticker}_1min_jun25_may26.pkl

Pickle structure: dict[date_iso_str] -> list of (datetime_et, o, h, l, c)
"""
import os, csv, gzip, pickle, glob, time
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

ET = ZoneInfo("America/New_York")
TICKER = os.environ.get("TICKER", "SPY")
EXCLUDE_CONDS = {37, 2, 52}

ARTIFACT_GLOB = f"/tmp/artifacts/{TICKER.lower()}_jun25_shard*/slim_trades_{TICKER.lower()}_jun25_shard*.csv.gz"
SHARDS = sorted(glob.glob(ARTIFACT_GLOB))
print(f"TICKER={TICKER} - found {len(SHARDS)} shards")

OUT = Path(f"data/minute_bars/{TICKER.lower()}_1min_jun25_may26.pkl")
OUT.parent.mkdir(exist_ok=True, parents=True)


def parse_conditions(s):
    if not s or s in ("[]", "None"): return frozenset()
    try:
        if s.startswith("["):
            import json
            return frozenset(json.loads(s))
        return frozenset(int(x) for x in s.strip().strip("[]").split(",") if x.strip())
    except Exception:
        return frozenset()


def parse_int(s, default=0):
    if not s: return default
    try: return int(s)
    except: 
        try: return int(float(s))
        except: return default


# date_str -> minute_bkt -> (o, h, l, c, last_ts)
day_bars = defaultdict(lambda: defaultdict(lambda: None))

total_kept = 0
for shard in SHARDS:
    t0 = time.time()
    print(f"  {shard}...", flush=True)
    try:
        with gzip.open(shard, "rt", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("ticker") != TICKER: continue
                try:
                    price = float(row["price"])
                except: continue
                conds = parse_conditions(row.get("conditions", ""))
                if EXCLUDE_CONDS & conds: continue
                sip = parse_int(row.get("sip_timestamp"))
                if sip == 0: continue
                # ms epoch
                ts_ms = sip // 1_000_000
                bkt_ms = (ts_ms // 60_000) * 60_000
                date_str = row.get("trade_date")
                cur = day_bars[date_str][bkt_ms]
                if cur is None:
                    day_bars[date_str][bkt_ms] = [price, price, price, price, ts_ms]
                else:
                    cur[1] = max(cur[1], price)  # h
                    cur[2] = min(cur[2], price)  # l
                    if ts_ms > cur[4]:
                        cur[3] = price            # c
                        cur[4] = ts_ms
                total_kept += 1
    except Exception as e:
        print(f"    err: {e}")
    print(f"    {time.time()-t0:.0f}s  running kept={total_kept:,}", flush=True)

# Convert to date_iso -> list of tuples
final = {}
for date_str, minutes in day_bars.items():
    rows = []
    for bkt_ms in sorted(minutes.keys()):
        dt = datetime.fromtimestamp(bkt_ms / 1000, tz=ET)
        # Only keep bars where minute matches our trade_date (avoid TZ rollover noise)
        if dt.date().isoformat() != date_str:
            continue
        # Pre/post + RTH (04:00 - 20:00)
        if dt.hour < 4 or dt.hour >= 20:
            continue
        o, h, l, c, _ = minutes[bkt_ms]
        rows.append((dt, o, h, l, c))
    if rows:
        final[date_str] = rows

print(f"\nBuilt {len(final)} days of minute bars for {TICKER}")
print(f"Sample first day: {min(final)} - {len(final[min(final)])} bars")
print(f"Sample last day:  {max(final)} - {len(final[max(final)])} bars")

with OUT.open("wb") as f:
    pickle.dump(final, f, protocol=pickle.HIGHEST_PROTOCOL)
print(f"Wrote {OUT} ({OUT.stat().st_size/1024/1024:.1f} MB)")
