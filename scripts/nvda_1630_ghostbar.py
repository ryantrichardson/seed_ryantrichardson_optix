"""
NVDA 5/21/2026 16:30 ET ghost bar analysis.
The chart shows a 5-min bar with O=H=$223.9545, L=$219.45, C=$219.74, vol=99.82k.
Ryan calls this a different kind of ghost bar -- a long red BODY pointing up,
not a wick. Body alone is ~$4.20. Distance from open to surrounding bars
(which are ~$220) is the "ghost" part.

Pull every trade 16:25-16:45 ET and answer:
1. Was there a real print at $223.9545 in this window?
2. What's the exchange/condition fingerprint of any high prints?
3. How does this differ from a long-tail wick (1-share TRF print) vs a real
   coordinated sell-the-pop event?
4. Volume profile -- did real volume happen at the high or at the low?
"""
import os, requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict, Counter

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})
ET = timezone(timedelta(hours=-4))

DAY = "2026-05-21"
TICKER = "NVDA"

d = datetime.strptime(DAY, "%Y-%m-%d")
start = datetime(d.year, d.month, d.day, 16, 0, tzinfo=ET)
end   = datetime(d.year, d.month, d.day, 17, 0, tzinfo=ET)

u = f"{BASE}/v3/trades/{TICKER}"
p = {"timestamp.gte": int(start.timestamp() * 1e9),
     "timestamp.lt":  int(end.timestamp() * 1e9),
     "limit": 50000, "order": "asc"}
trades = []
pages = 0
while u and pages < 100:
    r = S.get(u, params=p if pages == 0 else None, timeout=120)
    if r.status_code != 200:
        print(f"HTTP {r.status_code}"); break
    j = r.json()
    for t in j.get("results", []):
        ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
        if not ts_ns: continue
        ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(ET)
        trades.append({"t": ts, "price": t["price"], "size": t.get("size", 0),
                       "exchange": t.get("exchange"), "conditions": t.get("conditions") or [],
                       "trf_id": t.get("trf_id"),
                       "p_ts": t.get("participant_timestamp"),
                       "s_ts": t.get("sip_timestamp")})
    u = j.get("next_url"); p = None; pages += 1

print(f"Total NVDA trades 16:00-17:00 ET: {len(trades)}")

# Build 1-min bars 16:25-16:45 (no condition filtering -- after-hours rules differ)
by_min = defaultdict(list)
for t in trades:
    minute = t["t"].replace(second=0, microsecond=0)
    by_min[minute].append(t)

print("\n=== 1-min bars 16:25-16:45 (ALL trades, no condition filtering) ===")
print(f"{'Minute':6}  {'O':>9} {'H':>9} {'L':>9} {'C':>9}  {'n':>5}  {'vol':>7}")
for minute in sorted(by_min):
    if minute.hour != 16 or minute.minute < 25 or minute.minute > 45: continue
    ts = sorted(by_min[minute], key=lambda x: x["t"])
    prices = [t["price"] for t in ts]
    o, h, l, c = prices[0], max(prices), min(prices), prices[-1]
    vol = sum(t["size"] for t in by_min[minute])
    print(f"{minute.strftime('%H:%M')}  {o:>9.4f} {h:>9.4f} {l:>9.4f} {c:>9.4f}  {len(prices):>5}  {vol:>7}")

# Find the highest prints in the 16:30-16:35 window (matches the chart's 5-min bar)
window_530 = [t for t in trades if t["t"].hour == 16 and 30 <= t["t"].minute < 35]
print(f"\n=== 16:30-16:35 5-min bar (chart's red ghost bar) ===")
print(f"Total prints: {len(window_530)}  total shares: {sum(t['size'] for t in window_530):,}")
if window_530:
    prices = [t["price"] for t in window_530]
    print(f"O: {window_530[0]['price']}  H: {max(prices)}  L: {min(prices)}  C: {window_530[-1]['price']}")

# Highest 20 prints in window
print(f"\n=== Top 20 HIGHEST prints in 16:30-16:35 ===")
for t in sorted(window_530, key=lambda x: -x["price"])[:20]:
    gap_ms = (t["s_ts"] - t["p_ts"]) / 1e6 if t.get("p_ts") and t.get("s_ts") else 0
    tag = ""
    if t["exchange"] == 4:
        tag += " [TRF]"
        if 41 in t["conditions"]: tag += "+exempt"
        if 12 in t["conditions"]: tag += "+formT/AH"
    if 14 in t["conditions"]: tag += " [ISO]"
    if 12 in t["conditions"] and t["exchange"] != 4: tag += " [FormT/AH]"
    print(f"  {t['t'].strftime('%H:%M:%S.%f')[:-3]}  ${t['price']:>9.4f}  sz={t['size']:>6}  ex={t['exchange']:>3}  cond={t['conditions']}  gap={gap_ms:>6.1f}ms{tag}")

# What about the open of the bar specifically -- was the FIRST print at $223.95?
first5 = sorted(window_530, key=lambda x: x["t"])[:10]
print(f"\n=== First 10 prints of the 16:30-16:35 bar (the 'open' area) ===")
for t in first5:
    tag = " [TRF]" if t["exchange"]==4 else ""
    if 12 in t["conditions"]: tag += " [FormT/AH]"
    print(f"  {t['t'].strftime('%H:%M:%S.%f')[:-3]}  ${t['price']:>9.4f}  sz={t['size']:>5}  ex={t['exchange']:>3}  cond={t['conditions']}{tag}")

# Condition + exchange distribution in window
print(f"\n=== Condition code prevalence in 16:30-16:35 ===")
cond_count = Counter()
for t in window_530:
    for c in t["conditions"]:
        cond_count[c] += 1
for c, n in cond_count.most_common(10):
    print(f"  cond {c:>3}: {n} prints")

print(f"\n=== Exchange distribution in 16:30-16:35 ===")
ex_count = Counter(t["exchange"] for t in window_530)
ex_vol = defaultdict(int)
for t in window_530:
    ex_vol[t["exchange"]] += t["size"]
for ex, n in ex_count.most_common():
    print(f"  exchange {ex:>3}: {n} prints, {ex_vol[ex]:,} shares")

# Volume profile -- did real shares trade at the high or the low?
print(f"\n=== Volume profile (buckets of $0.25) ===")
vp = defaultdict(int)
for t in window_530:
    bucket = round(t["price"] * 4) / 4   # round to nearest 0.25
    vp[bucket] += t["size"]
for px in sorted(vp.keys()):
    bar = "#" * min(int(vp[px] / 200), 60)
    print(f"  ${px:>7.2f}  {vp[px]:>7,}  {bar}")

# Specifically: how many shares actually traded ABOVE $221?
high_trades = [t for t in window_530 if t["price"] >= 221]
print(f"\n=== Trades ABOVE $221 in 16:30-16:35 ===")
print(f"  n_prints: {len(high_trades)}  total shares: {sum(t['size'] for t in high_trades):,}  notional: ${sum(t['price']*t['size'] for t in high_trades):,.0f}")

# Comparison: what about the 16:25-16:30 bar (just BEFORE the ghost bar)?
window_525 = [t for t in trades if t["t"].hour == 16 and 25 <= t["t"].minute < 30]
print(f"\n=== Comparison: 16:25-16:30 bar (just before) ===")
if window_525:
    prices = [t["price"] for t in window_525]
    print(f"  O: {window_525[0]['price']}  H: {max(prices)}  L: {min(prices)}  C: {window_525[-1]['price']}")
    print(f"  n_prints: {len(window_525)}  total shares: {sum(t['size'] for t in window_525):,}")
