"""
TSLA accumulation-vs-distribution check via:
  1. VWAP-relative dark pool flow for 5/19 (today)
     - For each minute, compute that minute's VWAP from all lit + dark trades
     - Classify each dark trade as above/below that minute's VWAP
     - Roll up by 30-min bucket: % of dark $ above VWAP
  2. Day-by-day intraday block-dark % comparison for 5/11 through 5/19
     - For each session, compute intraday block-dark % in 30-min buckets
     - Show whether 5/19's 56% block-dark is structurally elevated or normal
"""
import os, requests, time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})
TICKER = "TSLA"

def fetch_day_trades(day):
    """Yield every trade for one trading day."""
    start_ns = int(datetime.combine(day, datetime.min.time(), timezone.utc).timestamp()*1e9)
    end_ns = int(datetime.combine(day+timedelta(days=1), datetime.min.time(), timezone.utc).timestamp()*1e9)
    u = f"{BASE}/v3/trades/{TICKER}"
    p = {"timestamp.gte":start_ns, "timestamp.lt":end_ns, "limit":50000, "order":"asc"}
    pages = 0
    while u and pages < 200:
        for attempt in range(5):
            try:
                r = S.get(u, params=p if pages==0 else None, timeout=120); break
            except Exception:
                time.sleep(1+attempt)
        if r.status_code != 200:
            print(f"  ! {day} page {pages} HTTP {r.status_code}: {r.text[:150]}")
            return
        j = r.json()
        for tr in j.get("results", []):
            yield tr
        u = j.get("next_url"); p = None; pages += 1

# =============================================================
# PART 1 — VWAP-relative dark flow for TODAY (5/19)
# =============================================================
today = datetime(2026, 5, 19).date()
print(f"=== PART 1: VWAP-relative dark pool flow for {today} ===\n")

# First pass: compute per-MINUTE VWAP from ALL trades (lit + dark)
minute_data = defaultdict(lambda: {"sum_pv": 0.0, "sum_v": 0})  # sum(price*size), sum(size)
all_trades = []
trade_count = 0

for tr in fetch_day_trades(today):
    sz = tr.get("size") or 0
    pr = tr.get("price") or 0
    ts = tr.get("sip_timestamp") or tr.get("participant_timestamp")
    if not ts or sz == 0 or pr == 0:
        continue
    dt = datetime.fromtimestamp(ts/1e9, timezone.utc) - timedelta(hours=4)  # ET
    minute_key = dt.replace(second=0, microsecond=0)
    minute_data[minute_key]["sum_pv"] += pr * sz
    minute_data[minute_key]["sum_v"] += sz
    is_dark = tr.get("exchange")==4 and tr.get("trf_id") is not None
    all_trades.append({"dt": dt, "minute": minute_key, "size": sz, "price": pr,
                       "notional": pr*sz, "is_dark": is_dark, "trf": tr.get("trf_id")})
    trade_count += 1
    if trade_count % 200000 == 0:
        print(f"  ... {trade_count:,} trades processed", flush=True)

print(f"\n  Total trades processed: {trade_count:,}")
print(f"  Unique minutes: {len(minute_data)}\n")

# Compute minute VWAPs
minute_vwap = {m: (d["sum_pv"]/d["sum_v"]) for m, d in minute_data.items() if d["sum_v"] > 0}

# Aggregate dark flow by 30-min bucket, splitting above/below minute VWAP
bucket_stats = defaultdict(lambda: {
    "dark_above": 0.0, "dark_below": 0.0, "dark_at": 0.0,
    "block_dark_above": 0.0, "block_dark_below": 0.0,
    "dark_trades": 0, "total_dark": 0.0,
    "first_p": None, "last_p": None, "high": 0, "low": 1e9,
})

for t in all_trades:
    bk = t["minute"].replace(minute=(t["minute"].minute // 30) * 30, second=0, microsecond=0)
    b = bucket_stats[bk]
    if b["first_p"] is None: b["first_p"] = t["price"]
    b["last_p"] = t["price"]
    if t["price"] > b["high"]: b["high"] = t["price"]
    if t["price"] < b["low"]: b["low"] = t["price"]
    if not t["is_dark"]:
        continue
    vwap = minute_vwap.get(t["minute"])
    if vwap is None:
        continue
    b["dark_trades"] += 1
    b["total_dark"] += t["notional"]
    if t["price"] > vwap:
        b["dark_above"] += t["notional"]
        if t["notional"] >= 100_000:
            b["block_dark_above"] += t["notional"]
    elif t["price"] < vwap:
        b["dark_below"] += t["notional"]
        if t["notional"] >= 100_000:
            b["block_dark_below"] += t["notional"]
    else:
        b["dark_at"] += t["notional"]

print(f"  {'BUCKET ET':<10} {'PRICE OHLC':>26} {'DARK $':>10} {'ABOVE VWAP':>14} {'BELOW VWAP':>14} {'ABOVE%':>7} {'BLK-ABOVE':>11} {'BLK-BELOW':>11} {'BLK-AB%':>8}")
session_dark_above = session_dark_below = 0
session_block_above = session_block_below = 0
for bk in sorted(bucket_stats.keys()):
    b = bucket_stats[bk]
    tot = b["dark_above"] + b["dark_below"]
    pct_above = (b["dark_above"] / tot * 100) if tot else 0
    blk_tot = b["block_dark_above"] + b["block_dark_below"]
    blk_pct_above = (b["block_dark_above"] / blk_tot * 100) if blk_tot else 0
    session_dark_above += b["dark_above"]
    session_dark_below += b["dark_below"]
    session_block_above += b["block_dark_above"]
    session_block_below += b["block_dark_below"]
    ohlc = f"{b['first_p']:.2f}/{b['high']:.2f}/{b['low']:.2f}/{b['last_p']:.2f}"
    print(f"  {bk.strftime('%H:%M'):<10} {ohlc:>26} ${b['total_dark']/1e6:>8,.1f}M ${b['dark_above']/1e6:>11,.1f}M ${b['dark_below']/1e6:>11,.1f}M {pct_above:>6.1f}% ${b['block_dark_above']/1e6:>8,.1f}M ${b['block_dark_below']/1e6:>8,.1f}M {blk_pct_above:>7.1f}%")

session_tot = session_dark_above + session_dark_below
session_blk_tot = session_block_above + session_block_below
print(f"\n  --- SESSION TOTAL ---")
print(f"  Dark above VWAP:  ${session_dark_above/1e6:>8,.0f}M  ({session_dark_above/session_tot*100:.2f}%)")
print(f"  Dark below VWAP:  ${session_dark_below/1e6:>8,.0f}M  ({session_dark_below/session_tot*100:.2f}%)")
print(f"  Net above-below:  ${(session_dark_above-session_dark_below)/1e6:+,.0f}M")
print(f"  Block dark above: ${session_block_above/1e6:>8,.0f}M  ({session_block_above/session_blk_tot*100:.2f}%)")
print(f"  Block dark below: ${session_block_below/1e6:>8,.0f}M  ({session_block_below/session_blk_tot*100:.2f}%)")
print(f"  Net block above-below: ${(session_block_above-session_block_below)/1e6:+,.0f}M")

# =============================================================
# PART 2 — Intraday block-dark % comparison across 7 days
# =============================================================
print(f"\n\n=== PART 2: Intraday block-dark % - 5/11 through 5/19 ===\n")

def trading_days_back(n, anchor):
    out, d = [], anchor
    while len(out) < n:
        if d.weekday() < 5: out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))

session_days = trading_days_back(7, today)
day_buckets = {}  # day -> bucket -> stats

for day in session_days:
    buckets = defaultdict(lambda: {"total_n":0.0, "dark_n":0.0, "blk_tot":0.0, "blk_dark":0.0, "first_p":None, "last_p":None})
    for tr in fetch_day_trades(day):
        sz = tr.get("size") or 0
        pr = tr.get("price") or 0
        ts = tr.get("sip_timestamp") or tr.get("participant_timestamp")
        if not ts or sz == 0 or pr == 0: continue
        dt = datetime.fromtimestamp(ts/1e9, timezone.utc) - timedelta(hours=4)
        # Bucket to 30-min
        bk = dt.replace(minute=(dt.minute // 30) * 30, second=0, microsecond=0)
        nn = sz * pr
        b = buckets[bk.strftime('%H:%M')]
        b["total_n"] += nn
        if b["first_p"] is None: b["first_p"] = pr
        b["last_p"] = pr
        is_dark = tr.get("exchange")==4 and tr.get("trf_id") is not None
        if is_dark: b["dark_n"] += nn
        if nn >= 100_000:
            b["blk_tot"] += nn
            if is_dark: b["blk_dark"] += nn
    day_buckets[day] = buckets
    # Day summary line
    tot_n = sum(b["total_n"] for b in buckets.values())
    tot_dark = sum(b["dark_n"] for b in buckets.values())
    tot_blk = sum(b["blk_tot"] for b in buckets.values())
    tot_blk_dark = sum(b["blk_dark"] for b in buckets.values())
    print(f"  {day}  $vol ${tot_n/1e6:>8,.0f}M  DPR {tot_dark/tot_n*100:5.2f}%  Block-DPR {tot_blk_dark/tot_blk*100 if tot_blk else 0:5.2f}%")

# Build a comparison grid for one representative bucket (e.g. last hour 15:30)
# Show DAILY: each 30-min bucket's block-dark % for each day side by side
print(f"\n  Intraday block-dark % grid (rows=time ET, cols=date)")
bucket_times = sorted(set(bk for d in day_buckets for bk in day_buckets[d]))
header = "  TIME    | " + " | ".join(str(d)[5:] for d in session_days)
print(header)
print("  " + "-" * (len(header) - 2))
for bt in bucket_times:
    if not (bt.startswith("09:") or bt.startswith("10:") or bt.startswith("11:") or bt.startswith("12:") or bt.startswith("13:") or bt.startswith("14:") or bt.startswith("15:")):
        continue
    row = f"  {bt}   |"
    for d in session_days:
        b = day_buckets[d].get(bt)
        if b and b["blk_tot"] > 0:
            row += f" {b['blk_dark']/b['blk_tot']*100:>5.1f} |"
        else:
            row += "    -  |"
    print(row)

# Quick stat: which days had block-dark > 60% in any bucket?
print(f"\n  Buckets where block-dark % > 60% (sustained institutional dark routing):")
for d in session_days:
    high_buckets = [(bt, day_buckets[d][bt]) for bt in sorted(day_buckets[d].keys())
                    if day_buckets[d][bt]["blk_tot"] > 0
                    and day_buckets[d][bt]["blk_dark"]/day_buckets[d][bt]["blk_tot"] > 0.60]
    print(f"  {d}: {len(high_buckets)} buckets > 60% — " + ", ".join(f"{bt} ({b['blk_dark']/b['blk_tot']*100:.0f}%)" for bt, b in high_buckets))
