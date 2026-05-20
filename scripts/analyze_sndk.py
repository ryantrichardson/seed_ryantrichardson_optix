"""TSLA dark pool intraday + multi-day read with focus on today's flush."""
import os, requests, time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})
TICKER = "SNDK"

print(f"=== {TICKER} dark pool read @ {datetime.now(timezone.utc).isoformat()} ===\n")

# ---- A. Last 7 trading days summary ----
def trading_days_back(n):
    out, d = [], datetime.now(timezone.utc).date()
    while len(out) < n:
        if d.weekday() < 5: out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))

print("[Daily summary - last 7 trading days]")
days = trading_days_back(7)
for day in days:
    start_ns = int(datetime.combine(day, datetime.min.time(), timezone.utc).timestamp()*1e9)
    end_ns = int(datetime.combine(day+timedelta(days=1), datetime.min.time(), timezone.utc).timestamp()*1e9)
    u = f"{BASE}/v3/trades/{TICKER}"
    p = {"timestamp.gte":start_ns, "timestamp.lt":end_ns, "limit":50000, "order":"asc"}
    total_n = dark_n = block_dark = block_tot = 0.0
    venue_n = {201:0.0, 202:0.0, 203:0.0}
    n_trades = n_dark = 0
    pages_t = 0
    while u and pages_t < 60:
        for attempt in range(5):
            try:
                resp = S.get(u, params=p if pages_t==0 else None, timeout=120); break
            except Exception: time.sleep(1+attempt)
        if resp.status_code != 200:
            print(f"  ! {day} HTTP {resp.status_code}"); break
        jj = resp.json()
        for tr in jj.get("results", []):
            sz = tr.get("size") or 0; pr = tr.get("price") or 0
            nn = sz*pr
            if nn == 0: continue
            n_trades += 1
            total_n += nn
            is_dark = tr.get("exchange")==4 and tr.get("trf_id") is not None
            if is_dark:
                n_dark += 1
                dark_n += nn
                if tr.get("trf_id") in venue_n: venue_n[tr.get("trf_id")] += nn
            if nn >= 100_000:
                block_tot += nn
                if is_dark: block_dark += nn
        u = jj.get("next_url"); p = None; pages_t += 1
    dpr = dark_n/total_n*100 if total_n else 0
    blk = block_dark/block_tot*100 if block_tot else 0
    cart = venue_n[202]/total_n*100 if total_n else 0
    print(f"  {day}  trades={n_trades:>8,}  $vol ${total_n/1e6:>8,.0f}M  dark ${dark_n/1e6:>7,.0f}M ({dpr:5.2f}%)  blocks ${block_tot/1e6:>7,.0f}M  blk-dark% {blk:5.2f}  cart% {cart:5.2f}")

# ---- B. Today intraday by 30-min bucket ----
print(f"\n[Today {days[-1]} - 30-min buckets, US/Eastern market hours]")
today = days[-1]
# Market hours 13:30 - 20:00 UTC (9:30-16:00 ET, but DST: 13:30-20:00 UTC during daylight time)
start_ns = int(datetime.combine(today, datetime.min.time(), timezone.utc).timestamp()*1e9) + (13*3600 + 30*60) * 10**9
end_ns = int(datetime.combine(today, datetime.min.time(), timezone.utc).timestamp()*1e9) + (20*3600) * 10**9

u = f"{BASE}/v3/trades/{TICKER}"
p = {"timestamp.gte":start_ns, "timestamp.lt":end_ns, "limit":50000, "order":"asc"}

buckets = defaultdict(lambda: {"total_n":0.0, "dark_n":0.0, "blk_tot":0.0, "blk_dark":0.0, "trades":0, "first_p":None, "last_p":None, "high":0, "low":1e9})

pages_t = 0
all_trades_today = []
while u and pages_t < 100:
    for attempt in range(5):
        try:
            resp = S.get(u, params=p if pages_t==0 else None, timeout=120); break
        except Exception: time.sleep(1+attempt)
    if resp.status_code != 200:
        print(f"  ! HTTP {resp.status_code}"); break
    jj = resp.json()
    for tr in jj.get("results", []):
        sz = tr.get("size") or 0
        pr = tr.get("price") or 0
        ts = tr.get("sip_timestamp") or tr.get("participant_timestamp")
        if not ts or sz == 0 or pr == 0: continue
        # Convert ns to datetime UTC, bucket to 30 min
        dt = datetime.fromtimestamp(ts/1e9, timezone.utc)
        # Round down to 30-min bucket
        bucket_min = (dt.minute // 30) * 30
        bk = dt.replace(minute=bucket_min, second=0, microsecond=0)
        # Convert to ET label
        et = bk - timedelta(hours=4)  # EDT
        nn = sz * pr
        b = buckets[et]
        b["total_n"] += nn
        b["trades"] += 1
        if b["first_p"] is None: b["first_p"] = pr
        b["last_p"] = pr
        if pr > b["high"]: b["high"] = pr
        if pr < b["low"]: b["low"] = pr
        is_dark = tr.get("exchange")==4 and tr.get("trf_id") is not None
        if is_dark:
            b["dark_n"] += nn
        if nn >= 100_000:
            b["blk_tot"] += nn
            if is_dark: b["blk_dark"] += nn
    u = jj.get("next_url"); p = None; pages_t += 1

print(f"  {'TIME ET':<10} {'TRADES':>8} {'$VOL':>10} {'DARK $':>10} {'DPR%':>6} {'BLK$':>10} {'BLK-DPR%':>9} {'OPEN':>7} {'HIGH':>7} {'LOW':>7} {'CLOSE':>7}")
for bk in sorted(buckets.keys()):
    b = buckets[bk]
    dpr = b["dark_n"]/b["total_n"]*100 if b["total_n"] else 0
    blk = b["blk_dark"]/b["blk_tot"]*100 if b["blk_tot"] else 0
    label = bk.strftime("%H:%M")
    print(f"  {label:<10} {b['trades']:>8,} ${b['total_n']/1e6:>8,.1f}M ${b['dark_n']/1e6:>8,.1f}M {dpr:>6.2f} ${b['blk_tot']/1e6:>8,.1f}M {blk:>9.2f} {b['first_p']:>7.2f} {b['high']:>7.2f} {b['low']:>7.2f} {b['last_p']:>7.2f}")

# ---- C. Biggest single trades today by notional ----
print(f"\n[Top 20 single trades today by $ notional]")
# Re-fetch but track individual trades
u = f"{BASE}/v3/trades/{TICKER}"
p = {"timestamp.gte":start_ns, "timestamp.lt":end_ns, "limit":50000, "order":"asc"}
top_trades = []
pages_t = 0
while u and pages_t < 100:
    for attempt in range(5):
        try:
            resp = S.get(u, params=p if pages_t==0 else None, timeout=120); break
        except Exception: time.sleep(1+attempt)
    if resp.status_code != 200: break
    jj = resp.json()
    for tr in jj.get("results", []):
        sz = tr.get("size") or 0; pr = tr.get("price") or 0
        nn = sz * pr
        if nn < 1_000_000: continue
        ts = tr.get("sip_timestamp") or tr.get("participant_timestamp")
        if not ts: continue
        dt = datetime.fromtimestamp(ts/1e9, timezone.utc) - timedelta(hours=4)
        is_dark = tr.get("exchange")==4 and tr.get("trf_id") is not None
        top_trades.append({
            "time": dt, "size": sz, "price": pr, "notional": nn,
            "is_dark": is_dark, "trf": tr.get("trf_id"), "exch": tr.get("exchange")
        })
    u = jj.get("next_url"); p = None; pages_t += 1

top_trades.sort(key=lambda x: -x["notional"])
print(f"  {'TIME ET':<10} {'SIZE':>10} {'PRICE':>8} {'$ NOTIONAL':>14} {'VENUE':<15}")
for t in top_trades[:20]:
    venue = "DARK (TRF " + str(t["trf"]) + ")" if t["is_dark"] else f"lit (exch {t['exch']})"
    print(f"  {t['time'].strftime('%H:%M:%S')} {t['size']:>10,} {t['price']:>8.2f} ${t['notional']:>12,.0f} {venue:<15}")

# ---- D. Around-the-flush focus: 13:00-14:30 ET deep dive ----
print(f"\n[FLUSH ZONE - 13:00-14:30 ET, by 5-min bucket]")
# 13:00 ET = 17:00 UTC, 14:30 ET = 18:30 UTC
flush_start_ns = int(datetime.combine(today, datetime.min.time(), timezone.utc).timestamp()*1e9) + (17*3600) * 10**9
flush_end_ns = int(datetime.combine(today, datetime.min.time(), timezone.utc).timestamp()*1e9) + (18*3600 + 30*60) * 10**9

u = f"{BASE}/v3/trades/{TICKER}"
p = {"timestamp.gte":flush_start_ns, "timestamp.lt":flush_end_ns, "limit":50000, "order":"asc"}

flush_buckets = defaultdict(lambda: {"total_n":0.0, "dark_n":0.0, "blk_tot":0.0, "blk_dark":0.0, "trades":0, "first_p":None, "last_p":None, "high":0, "low":1e9, "up_v":0, "dn_v":0, "last_seen":None})
pages_t = 0
while u and pages_t < 40:
    for attempt in range(5):
        try:
            resp = S.get(u, params=p if pages_t==0 else None, timeout=120); break
        except Exception: time.sleep(1+attempt)
    if resp.status_code != 200: break
    jj = resp.json()
    for tr in jj.get("results", []):
        sz = tr.get("size") or 0; pr = tr.get("price") or 0
        ts = tr.get("sip_timestamp") or tr.get("participant_timestamp")
        if not ts or sz == 0 or pr == 0: continue
        dt = datetime.fromtimestamp(ts/1e9, timezone.utc) - timedelta(hours=4)
        bucket_min = (dt.minute // 5) * 5
        bk = dt.replace(minute=bucket_min, second=0, microsecond=0)
        nn = sz*pr
        b = flush_buckets[bk]
        b["total_n"] += nn
        b["trades"] += 1
        if b["first_p"] is None: b["first_p"] = pr
        if b["last_seen"] is not None:
            if pr > b["last_seen"]: b["up_v"] += sz
            elif pr < b["last_seen"]: b["dn_v"] += sz
        b["last_seen"] = pr
        b["last_p"] = pr
        if pr > b["high"]: b["high"] = pr
        if pr < b["low"]: b["low"] = pr
        is_dark = tr.get("exchange")==4 and tr.get("trf_id") is not None
        if is_dark: b["dark_n"] += nn
        if nn >= 100_000:
            b["blk_tot"] += nn
            if is_dark: b["blk_dark"] += nn
    u = jj.get("next_url"); p = None; pages_t += 1

print(f"  {'TIME ET':<8} {'TRADES':>7} {'$VOL':>8} {'DARK $':>8} {'DPR%':>6} {'BLK$':>8} {'BLK-DPR%':>9} {'PRICE':>10} {'UP/DN VOL':>14}")
for bk in sorted(flush_buckets.keys()):
    b = flush_buckets[bk]
    dpr = b["dark_n"]/b["total_n"]*100 if b["total_n"] else 0
    blk = b["blk_dark"]/b["blk_tot"]*100 if b["blk_tot"] else 0
    label = bk.strftime("%H:%M")
    px = f"{b['first_p']:.2f}->{b['last_p']:.2f}"
    ud = f"{b['up_v']:>5,}/{b['dn_v']:>5,}"
    print(f"  {label:<8} {b['trades']:>7,} ${b['total_n']/1e6:>6,.1f}M ${b['dark_n']/1e6:>6,.1f}M {dpr:>6.2f} ${b['blk_tot']/1e6:>6,.1f}M {blk:>9.2f} {px:>10} {ud:>14}")
