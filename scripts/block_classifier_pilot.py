"""
Block classifier pilot — for each of N wicks, pull every trade at/near the wick
extreme price within ±2 minutes of the wick timestamp, then compute:

  - total_notional      : sum of price*size for all prints at the extreme
  - total_shares        : sum of shares
  - max_print_size      : largest single print
  - n_round_lots        : # prints with size >= 100
  - round_lot_shares    : shares from round lots only
  - frag_shares         : shares from odd lots (size < 100)
  - n_prints            : total prints
  - venues              : unique exchanges
  - n_trf               : # prints on exchange 4 (TRF/off-exchange)
  - max_single_notional : largest single print in $

Then output verdict: BLOCK vs FRAGMENTED vs MIXED, plus hit/miss label.

The hypothesis to test:
  BLOCK wicks (real hidden institutional flow) should have higher hit rate than
  FRAGMENTED wicks (retail bookouts/admin prints).
"""
import os, json, requests, csv
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})
ET = timezone(timedelta(hours=-4))

samples = json.load(open("data/pilot_block_classifier.json"))

# How close to the extreme price counts as "the wick"? Use 0.05% tolerance.
# (e.g. if extreme is $415.76, accept prints from $415.55 to $415.97)
PRICE_TOL_PCT = 0.05  # ±0.05% of extreme

def get_window_trades(ticker, dt, minute_offset_back=2, minute_offset_fwd=2):
    """Pull trades for ticker in window around dt."""
    start = dt - timedelta(minutes=minute_offset_back)
    end   = dt + timedelta(minutes=minute_offset_fwd + 1)
    u = f"{BASE}/v3/trades/{ticker}"
    p = {"timestamp.gte": int(start.timestamp() * 1e9),
         "timestamp.lt":  int(end.timestamp() * 1e9),
         "limit": 50000, "order": "asc"}
    trades = []
    pages = 0
    while u and pages < 100:
        r = S.get(u, params=p if pages == 0 else None, timeout=120)
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}: {r.text[:100]}")
            return None
        j = r.json()
        for t in j.get("results", []):
            ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
            if not ts_ns: continue
            ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(ET)
            trades.append({"t": ts, "price": t["price"], "size": t.get("size", 0),
                           "exchange": t.get("exchange"), "conditions": t.get("conditions") or [],
                           "trf_id": t.get("trf_id")})
        u = j.get("next_url"); p = None; pages += 1
    return trades

def classify(trades_at_extreme):
    if not trades_at_extreme:
        return None
    total_shares = sum(t["size"] for t in trades_at_extreme)
    total_notional = sum(t["price"] * t["size"] for t in trades_at_extreme)
    max_print = max(trades_at_extreme, key=lambda t: t["size"])
    max_size = max_print["size"]
    max_notional = max_print["price"] * max_print["size"]
    round_lots = [t for t in trades_at_extreme if t["size"] >= 100]
    n_round = len(round_lots)
    round_shares = sum(t["size"] for t in round_lots)
    frag_shares = total_shares - round_shares
    venues = set(t["exchange"] for t in trades_at_extreme)
    n_trf = sum(1 for t in trades_at_extreme if t["exchange"] == 4)

    # Verdict:
    #   BLOCK         : total_notional >= $1M  AND max_print_size >= 1000
    #   LARGE_INSTIT  : total_notional >= $250k AND max_print_size >= 500
    #   SMALL_BLOCK   : total_notional >= $50k  AND max_print_size >= 100
    #   FRAGMENTED    : everything else
    if total_notional >= 1_000_000 and max_size >= 1000:
        verdict = "BLOCK"
    elif total_notional >= 250_000 and max_size >= 500:
        verdict = "LARGE_INSTIT"
    elif total_notional >= 50_000 and max_size >= 100:
        verdict = "SMALL_BLOCK"
    else:
        verdict = "FRAGMENTED"

    return {
        "verdict": verdict,
        "n_prints": len(trades_at_extreme),
        "total_shares": total_shares,
        "total_notional": round(total_notional, 0),
        "max_print_size": max_size,
        "max_print_notional": round(max_notional, 0),
        "n_round_lots": n_round,
        "round_lot_shares": round_shares,
        "frag_shares": frag_shares,
        "venues": sorted(venues),
        "n_trf_prints": n_trf,
        "trf_share_pct": round(n_trf / len(trades_at_extreme) * 100, 1),
    }

results = []
for s in samples:
    tk = s["ticker"]; date = s["date"]; tm = s["time_et"]
    extreme = float(s["extreme"]); direction = s["direction"]
    touched = s["touched"] == "True"
    days = s.get("days_to_touch", "")

    dt = datetime.strptime(f"{date} {tm}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)

    print(f"\n=== {tk} {date} {tm} {direction.upper()} ${extreme:.2f} ({float(s['wick_pct']):.2f}%) touched={touched} days={days} ===")
    trades = get_window_trades(tk, dt)
    if trades is None:
        results.append({**s, "verdict": "ERROR"})
        continue

    # Filter to prints near the wick extreme
    tol = extreme * PRICE_TOL_PCT / 100
    if direction == "down":
        # for down wicks, we want prints at or below extreme + tol
        near = [t for t in trades if t["price"] <= extreme + tol and t["price"] >= extreme - tol]
    else:
        near = [t for t in trades if t["price"] >= extreme - tol and t["price"] <= extreme + tol]

    cls = classify(near)
    if cls is None:
        print(f"  No prints found near ${extreme}")
        results.append({**s, "verdict": "NO_PRINTS_NEAR_EXTREME"})
        continue

    print(f"  Verdict: {cls['verdict']}")
    print(f"  n_prints={cls['n_prints']}  total_shares={cls['total_shares']:,}  notional=${cls['total_notional']:,.0f}")
    print(f"  max_single: {cls['max_print_size']:,} sh = ${cls['max_print_notional']:,.0f}")
    print(f"  round_lots: {cls['n_round_lots']} ({cls['round_lot_shares']:,} sh) | frag: {cls['frag_shares']:,} sh")
    print(f"  venues: {cls['venues']}  TRF%: {cls['trf_share_pct']}%")

    results.append({**s, **cls})

# Write CSV
keys = ["ticker","date","time_et","direction","wick_pct","extreme","touched","days_to_touch",
        "verdict","n_prints","total_shares","total_notional","max_print_size","max_print_notional",
        "n_round_lots","round_lot_shares","frag_shares","venues","n_trf_prints","trf_share_pct"]
with open("data/pilot_block_classifier_results.csv","w",newline="") as f:
    w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
    w.writeheader()
    for r in results:
        if isinstance(r.get("venues"), list):
            r["venues"] = "|".join(str(v) for v in r["venues"])
        w.writerow(r)

# Summary by verdict + hit/miss
print("\n\n" + "="*70)
print("PILOT SUMMARY: hit rate by verdict")
print("="*70)
buckets = defaultdict(lambda: {"hit":0,"miss":0})
for r in results:
    v = r.get("verdict","?")
    if r.get("touched") == "True" or r.get("touched") is True:
        buckets[v]["hit"] += 1
    else:
        buckets[v]["miss"] += 1
for v, b in buckets.items():
    n = b["hit"] + b["miss"]
    rate = b["hit"]/n*100 if n else 0
    print(f"  {v:18}  n={n}  hits={b['hit']}  misses={b['miss']}  rate={rate:.1f}%")

print("\nWritten: data/pilot_block_classifier_results.csv")
