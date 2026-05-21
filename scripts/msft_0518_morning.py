"""
Pull MSFT 5/18 from 9:30-10:30 ET morning to verify:
1. Did the stock actually trade at $415.76 anywhere in the morning?
2. Was the morning low on lit exchanges?
3. Are the 13:45 $415.76 prints LATE REPORTS of morning trades, or something else?
4. Also pull 13:00-14:00 to look at the conditions on those prints in detail.
"""
import os, requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})
ET = timezone(timedelta(hours=-4))
TICKER = "MSFT"
DAY = "2026-05-18"

def get_trades(ticker, day_str, hr_start, hr_end):
    d = datetime.strptime(day_str, "%Y-%m-%d")
    start = datetime(d.year, d.month, d.day, hr_start, 0, tzinfo=ET)
    end   = datetime(d.year, d.month, d.day, hr_end,  0, tzinfo=ET)
    u = f"{BASE}/v3/trades/{ticker}"
    p = {"timestamp.gte": int(start.timestamp() * 1e9),
         "timestamp.lt":  int(end.timestamp() * 1e9),
         "limit": 50000, "order": "asc"}
    trades = []
    pages = 0
    while u and pages < 400:
        r = S.get(u, params=p if pages == 0 else None, timeout=120)
        if r.status_code != 200: print(f"HTTP {r.status_code}"); break
        j = r.json()
        for t in j.get("results", []):
            ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
            if not ts_ns: continue
            ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(ET)
            trades.append({
                "t": ts, "price": t["price"], "size": t.get("size", 0),
                "exchange": t.get("exchange"), "conditions": t.get("conditions") or [],
                "trf_id": t.get("trf_id"),
                "sip_ts": t.get("sip_timestamp"),
                "participant_ts": t.get("participant_timestamp"),
            })
        u = j.get("next_url"); p = None; pages += 1
    return trades

# Morning trades
trades_am = get_trades(TICKER, DAY, 9, 11)
print(f"Total trades 9:00-11:00: {len(trades_am)}")

# Find ALL trades at or near $415.76 in the morning (regardless of conditions)
print(f"\n=== Trades at $415.50-$416.00 in 9:00-11:00 (n_searched={len(trades_am)}) ===")
hits = [t for t in trades_am if 415.50 <= t["price"] <= 416.00]
print(f"Found {len(hits)} prints in that band")
# Show distribution by exchange and conditions
by_ex = defaultdict(int)
by_cond_flag = defaultdict(int)
for t in hits:
    by_ex[t["exchange"]] += 1
    if t["exchange"] == 4 and 41 in t["conditions"]:
        by_cond_flag["TRF cond41"] += 1
    elif t["exchange"] == 4:
        by_cond_flag["TRF other"] += 1
    elif 14 in t["conditions"]:
        by_cond_flag["ISO SWEEP"] += 1
    else:
        by_cond_flag["lit"] += 1
print(f"By exchange: {dict(by_ex)}")
print(f"By type: {dict(by_cond_flag)}")

# Lowest 20 LIT prints
print(f"\n=== Lowest 20 LIT (non-TRF, non-noise-cond) trades 9:00-11:00 ===")
lit = [t for t in trades_am if t["exchange"] != 4 and not (set(t["conditions"]) & {2,12,16,33,52,53})]
for t in sorted(lit, key=lambda x: x["price"])[:20]:
    tag = " [ISO SWEEP]" if 14 in t["conditions"] else ""
    print(f"  {t['t'].strftime('%H:%M:%S.%f')[:-3]}  ${t['price']:>8.4f}  sz={t['size']:>5}  ex={t['exchange']:>3}  cond={t['conditions']}{tag}")

# Now look at the 13:45 batch more carefully — show participant vs SIP timestamp gap
print(f"\n=== 13:45:25 $415.76 prints — participant vs SIP timestamp gap ===")
trades_pm = get_trades(TICKER, DAY, 13, 14)
batch = [t for t in trades_pm if t["t"].hour == 13 and t["t"].minute == 45 and abs(t["price"] - 415.76) < 0.01]
print(f"Found {len(batch)} prints at $415.76 in 13:45")
for t in batch[:15]:
    p_ts = t.get("participant_ts")
    s_ts = t.get("sip_ts")
    if p_ts and s_ts:
        gap_ms = (s_ts - p_ts) / 1e6
        p_time = datetime.fromtimestamp(p_ts/1e9, tz=timezone.utc).astimezone(ET).strftime('%H:%M:%S.%f')[:-3]
        s_time = datetime.fromtimestamp(s_ts/1e9, tz=timezone.utc).astimezone(ET).strftime('%H:%M:%S.%f')[:-3]
        print(f"  participant={p_time}  sip={s_time}  gap={gap_ms:>8.1f}ms  ${t['price']}  sz={t['size']:>4}  cond={t['conditions']}")

# What is conditions 22? Look it up - average price trade
print(f"\n=== Condition code distribution for the 13:45 batch ===")
cond_dist = defaultdict(int)
size_dist = defaultdict(int)
for t in batch:
    cond_dist[tuple(sorted(t["conditions"]))] += 1
    size_dist[t["size"]] += 1
print(f"Conditions: {dict(cond_dist)}")
print(f"Sizes: {dict(sorted(size_dist.items()))}")
print(f"Total volume: {sum(t['size'] for t in batch)}")
print(f"Unique exchanges: {set(t['exchange'] for t in batch)}")
print(f"Unique TRF IDs: {set(t.get('trf_id') for t in batch)}")
