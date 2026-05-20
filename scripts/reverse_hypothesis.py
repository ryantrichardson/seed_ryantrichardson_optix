"""
Reverse hypothesis: does an extreme 5%+ ghost wick predict price moving in
the OPPOSITE direction (the wick marks a reversal/turning point)?

For each ghost wick in the CSV:
  - Reference close = the body close of the ghost candle
  - If direction='down', test if price moves UP by N% within 10 days
  - If direction='up', test if price moves DOWN by N% within 10 days
  - Track max favorable excursion in opposite direction

Also test the contrary trade: for down-wicks where price did NOT touch the
extreme, did it instead move UP significantly? (This is the "ghost wick marks
the bottom" trade.)
"""
import os, csv, sys
from datetime import datetime, timedelta
import requests

API = os.environ.get("MASSIVE_API_KEY", "")
BASE = "https://api.massive.com"
S = requests.Session()
if API:
    S.headers.update({"Authorization": f"Bearer {API}"})

TICKER = os.environ.get("TICKER", "TSLA")
LOOKFORWARD = 10

csv_path = f"data/ghost_wicks_v2_{TICKER}_trade.csv"
if not os.path.exists(csv_path):
    print(f"ERROR: {csv_path} not found. Run ghost_v2 workflow first.")
    sys.exit(1)

# Load forward daily bars for hit detection (broader range to cover lookforward)
print("Fetching forward daily bars...")
r = S.get(f"{BASE}/v2/aggs/ticker/{TICKER}/range/1/day/2025-11-01/2026-12-31",
          params={"limit": 5000}, timeout=30)
days_list = []
day_bars = {}
for d in r.json().get("results", []):
    dt = datetime.fromtimestamp(d['t']/1000).date()
    day_bars[dt] = {"o": d['o'], "h": d['h'], "l": d['l'], "c": d['c']}
    days_list.append(dt)
days_list.sort()
day_idx = {d: i for i, d in enumerate(days_list)}

print(f"Loaded {len(days_list)} daily bars")

# Load wicks
wicks = []
with open(csv_path) as f:
    for r in csv.DictReader(f):
        r["close"] = float(r["close"])
        r["extreme"] = float(r["extreme"])
        r["wick_pct"] = float(r["wick_pct"])
        r["ratio"] = float(r["ratio"])
        r["touched"] = r["touched"] == "True"
        wicks.append(r)

print(f"Loaded {len(wicks)} ghost wicks")

# Filter to extreme wicks (5%+)
extreme = [w for w in wicks if w["wick_pct"] >= 5.0]
print(f"Extreme (5%+) wicks: {len(extreme)}")

# For each extreme wick, compute forward returns in BOTH directions
results = []
for w in extreme:
    d = datetime.strptime(w["date"], "%Y-%m-%d").date()
    if d not in day_idx:
        continue
    start_i = day_idx[d]

    body_close = w["close"]
    direction = w["direction"]
    extreme_price = w["extreme"]

    # Look forward up to LOOKFORWARD days
    max_up_move = 0.0   # max % above body_close
    max_dn_move = 0.0   # max % below body_close
    days_until_max_up = None
    days_until_max_dn = None
    final_close = None
    for offset in range(1, LOOKFORWARD + 1):
        if start_i + offset >= len(days_list):
            break
        fwd = days_list[start_i + offset]
        bar = day_bars.get(fwd)
        if not bar:
            continue
        up = (bar["h"] - body_close) / body_close * 100
        dn = (body_close - bar["l"]) / body_close * 100
        if up > max_up_move:
            max_up_move = up
            days_until_max_up = offset
        if dn > max_dn_move:
            max_dn_move = dn
            days_until_max_dn = offset
        final_close = bar["c"]

    if final_close is None:
        continue

    final_return = (final_close - body_close) / body_close * 100
    # "Reverse" direction = opposite of wick direction
    if direction == "down":
        reverse_move = max_up_move
        days_to_reverse = days_until_max_up
    else:
        reverse_move = max_dn_move
        days_to_reverse = days_until_max_dn

    results.append({
        **w,
        "max_up_move_pct": round(max_up_move, 2),
        "max_dn_move_pct": round(max_dn_move, 2),
        "reverse_move_pct": round(reverse_move, 2),
        "days_to_reverse_max": days_to_reverse,
        "final_return_pct": round(final_return, 2),
    })

print(f"\n=== Reverse-direction analysis (5%+ wicks) ===")
print(f"Sample size: {len(results)}")

# How often did reverse move exceed thresholds?
for threshold in [1, 2, 3, 5, 10]:
    n = sum(1 for r in results if r["reverse_move_pct"] >= threshold)
    pct = 100*n/max(len(results),1)
    print(f"  Reverse move >= {threshold}%: {n}/{len(results)} ({pct:.1f}%)")

# By direction
for direction in ["up", "down"]:
    sub = [r for r in results if r["direction"] == direction]
    if not sub: continue
    avg_rev = sum(r["reverse_move_pct"] for r in sub) / len(sub)
    avg_final = sum(r["final_return_pct"] for r in sub) / len(sub)
    print(f"\n  {direction}-wicks (n={len(sub)}):")
    print(f"    Avg max reverse move: {avg_rev:.2f}%")
    print(f"    Avg 10-day return: {avg_final:.2f}%")

# Did the failed-to-touch wicks reverse strongly?
no_touch = [r for r in results if not r["touched"]]
touched = [r for r in results if r["touched"]]
print(f"\n=== Of the {len(results)} extreme wicks ===")
print(f"  Touched the extreme: {len(touched)}")
print(f"  Did NOT touch extreme: {len(no_touch)}")
if no_touch:
    avg_rev_nt = sum(r["reverse_move_pct"] for r in no_touch) / len(no_touch)
    print(f"  Of non-touching wicks, avg reverse move: {avg_rev_nt:.2f}%")
    strong_reverse = sum(1 for r in no_touch if r["reverse_move_pct"] >= 5)
    print(f"  Non-touching wicks with >=5% reverse move: {strong_reverse}/{len(no_touch)} "
          f"({100*strong_reverse/len(no_touch):.1f}%)")

# Show each example
print(f"\n=== All 5%+ wicks: outcomes ===")
results.sort(key=lambda x: x["date"])
for r in results:
    outcome = f"TOUCH d{r['days_to_touch']}" if r["touched"] else "NO TOUCH"
    print(f"  {r['date']} {r['time_et']} {r['direction']} ${r['extreme']} ({r['wick_pct']}%) "
          f"-> {outcome} | reverse {r['reverse_move_pct']}% | 10d final {r['final_return_pct']}%")

# Save
out_path = f"data/reverse_hypothesis_{TICKER}.csv"
if results:
    with open(out_path, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        wr.writeheader()
        wr.writerows(results)
    print(f"\nSaved {out_path}")
