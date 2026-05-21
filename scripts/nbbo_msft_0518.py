"""
NBBO-aware wick classifier.
Pull MSFT quotes (NBBO ticks) around 13:45:25 ET on 2026-05-18 and answer:
  - What was the inside bid/ask at the millisecond the $415.76 batch printed?
  - How far below the NBB was the print? (in $ and bps)
  - Was the spread normal or wide at that moment?
  - Show NBBO 13:45:00 through 13:46:00 for context.
"""
import os, requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})
ET = timezone(timedelta(hours=-4))

DAY = "2026-05-18"
TICKER = "MSFT"
BATCH_TIME = "13:45:25.534"  # the millisecond the $415.76 prints fired
PRINT_PRICE = 415.76

# Pull NBBO quotes around 13:45 (40 sec window)
d = datetime.strptime(DAY, "%Y-%m-%d")
start = datetime(d.year, d.month, d.day, 13, 45, 0, tzinfo=ET)
end   = datetime(d.year, d.month, d.day, 13, 46, 0, tzinfo=ET)

u = f"{BASE}/v3/quotes/{TICKER}"
p = {"timestamp.gte": int(start.timestamp() * 1e9),
     "timestamp.lt":  int(end.timestamp() * 1e9),
     "limit": 50000, "order": "asc"}
quotes = []
pages = 0
while u and pages < 50:
    r = S.get(u, params=p if pages == 0 else None, timeout=60)
    if r.status_code != 200: print(f"HTTP {r.status_code}: {r.text[:200]}"); break
    j = r.json()
    for q in j.get("results", []):
        ts_ns = q.get("participant_timestamp") or q.get("sip_timestamp")
        if not ts_ns: continue
        ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(ET)
        quotes.append({
            "t": ts,
            "ts_ns": ts_ns,
            "bid": q.get("bid_price"),
            "ask": q.get("ask_price"),
            "bid_size": q.get("bid_size"),
            "ask_size": q.get("ask_size"),
            "bid_exchange": q.get("bid_exchange"),
            "ask_exchange": q.get("ask_exchange"),
            "conditions": q.get("conditions") or [],
            "indicators": q.get("indicators") or [],
        })
    u = j.get("next_url"); p = None; pages += 1

print(f"Pulled {len(quotes)} NBBO ticks 13:45:00–13:46:00")

# Find quote just before/at 13:45:25.534
target_ts = datetime(d.year, d.month, d.day, 13, 45, 25, 534000, tzinfo=ET)
target_ns = int(target_ts.timestamp() * 1e9)

# Quote in effect at the moment of the print
in_effect = None
for q in quotes:
    if q["ts_ns"] <= target_ns:
        in_effect = q
    else:
        break

print(f"\n=== NBBO at moment of print ({BATCH_TIME}) ===")
if in_effect:
    bid, ask = in_effect["bid"], in_effect["ask"]
    spread = ask - bid
    mid = (bid + ask) / 2
    dist_below_bid = bid - PRINT_PRICE
    dist_pct = dist_below_bid / mid * 100
    dist_bps = dist_below_bid / mid * 10000
    print(f"  Quote time:        {in_effect['t'].strftime('%H:%M:%S.%f')[:-3]}")
    print(f"  NBB (bid):         ${bid:.4f} (size {in_effect['bid_size']}, ex {in_effect['bid_exchange']})")
    print(f"  NBO (ask):         ${ask:.4f} (size {in_effect['ask_size']}, ex {in_effect['ask_exchange']})")
    print(f"  Spread:            ${spread:.4f} ({spread/mid*10000:.1f} bps)")
    print(f"  Midpoint:          ${mid:.4f}")
    print(f"  Print price:       ${PRINT_PRICE:.4f}")
    print(f"  Distance below NBB: ${dist_below_bid:.4f}  ({dist_pct:.3f}% / {dist_bps:.0f} bps)")
    print(f"  Trade-through?      {'YES' if PRINT_PRICE < bid else 'NO'}")

# Show NBBO range across the full minute
print(f"\n=== NBBO bid/ask range across 13:45:00–13:46:00 ===")
bids = [q["bid"] for q in quotes if q["bid"]]
asks = [q["ask"] for q in quotes if q["ask"]]
if bids and asks:
    print(f"  NBB range:  ${min(bids):.4f}  to  ${max(bids):.4f}")
    print(f"  NBO range:  ${min(asks):.4f}  to  ${max(asks):.4f}")
    print(f"  Min NBB in this minute: ${min(bids):.4f}  (vs print at ${PRINT_PRICE:.4f}: ${min(bids)-PRINT_PRICE:.4f} above)")

# Look for any sub-$418 quote in the day
print(f"\n=== Did NBB ever go below $420 anywhere in 13:45–13:46? ===")
low_bids = sorted([q for q in quotes if q["bid"] and q["bid"] < 420], key=lambda x: x["bid"])
print(f"  Quotes with bid < $420: {len(low_bids)}")
for q in low_bids[:5]:
    print(f"    {q['t'].strftime('%H:%M:%S.%f')[:-3]}  bid=${q['bid']}  ask=${q['ask']}  cond={q['conditions']}")

# Show a sample of quotes throughout the minute
print(f"\n=== NBBO snapshots (every ~10s) ===")
last_shown = None
for q in quotes:
    if last_shown is None or (q["t"] - last_shown).total_seconds() >= 10:
        spread = q["ask"] - q["bid"] if q["bid"] and q["ask"] else None
        print(f"  {q['t'].strftime('%H:%M:%S.%f')[:-3]}  bid=${q['bid']}  ask=${q['ask']}  spread=${spread:.4f}" if spread else "")
        last_shown = q["t"]
