"""Multi-signal read on SLV: dark pool flow + options chain + price/volume context."""
import os, sys, requests, time
from datetime import datetime, timedelta, timezone
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

TICKER = "SLV"

def get(path, params=None):
    r = S.get(f"{BASE}{path}", params=params, timeout=60)
    return r.status_code, (r.json() if r.headers.get("content-type","").startswith("application/json") else {})

print(f"=== {TICKER} multi-signal read @ {datetime.now(timezone.utc).isoformat()} ===\n")

# ---- 1. Last 10 trading days of price + volume ----
end = datetime.now(timezone.utc).date()
start = end - timedelta(days=20)
sc, j = get(f"/v2/aggs/ticker/{TICKER}/range/1/day/{start}/{end}", {"adjusted":"true","sort":"asc"})
bars = j.get("results", [])
print(f"[Price/Volume] last {len(bars)} daily bars")
for b in bars[-10:]:
    d = datetime.fromtimestamp(b["t"]/1000, timezone.utc).date()
    print(f"  {d}  O={b['o']:.2f}  H={b['h']:.2f}  L={b['l']:.2f}  C={b['c']:.2f}  V={b['v']:>12,.0f}  VWAP={b.get('vw',0):.2f}")
if len(bars) >= 2:
    chg = (bars[-1]["c"]/bars[-2]["c"]-1)*100
    chg5 = (bars[-1]["c"]/bars[-6]["c"]-1)*100 if len(bars)>=6 else None
    chg20 = (bars[-1]["c"]/bars[0]["c"]-1)*100
    avg_vol = sum(b["v"] for b in bars[-20:]) / min(20, len(bars))
    last_vol_ratio = bars[-1]["v"] / avg_vol
    print(f"\n  1d change: {chg:+.2f}%   5d change: {chg5:+.2f}%   20d change: {chg20:+.2f}%")
    print(f"  Last volume vs 20d avg: {last_vol_ratio:.2f}x")

# ---- 2. Dark pool flow: last 5 trading days ----
print(f"\n[Dark Pool Trades] last 5 days")
def trading_days_back(n):
    out, d = [], datetime.now(timezone.utc).date() - timedelta(days=1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))

dp_summary = []
for day in trading_days_back(5):
    start_ns = int(datetime.combine(day, datetime.min.time(), timezone.utc).timestamp()*1e9)
    end_ns = int(datetime.combine(day+timedelta(days=1), datetime.min.time(), timezone.utc).timestamp()*1e9)
    url = f"{BASE}/v3/trades/{TICKER}"
    params = {"timestamp.gte":start_ns, "timestamp.lt":end_ns, "limit":50000, "order":"asc"}
    total_not = 0.0
    dark_not = 0.0
    block_dark_not = 0.0
    block_total_not = 0.0
    venue = defaultdict(float)
    pages = 0
    while url and pages < 25:
        r = S.get(url, params=params if pages==0 else None, timeout=90)
        if r.status_code != 200:
            print(f"  ! {day} HTTP {r.status_code}")
            break
        j = r.json()
        for tr in j.get("results",[]):
            size = tr.get("size") or 0
            price = tr.get("price") or 0
            n = size*price
            if n == 0: continue
            total_not += n
            is_dark = tr.get("exchange")==4 and tr.get("trf_id") is not None
            if is_dark:
                dark_not += n
                venue[tr.get("trf_id")] += n
            if n >= 100_000:
                block_total_not += n
                if is_dark:
                    block_dark_not += n
        url = j.get("next_url")
        params = None
        pages += 1
    dpr = (dark_not/total_not*100) if total_not else 0
    blk = (block_dark_not/block_total_not*100) if block_total_not else 0
    cart = (venue[202]/total_not*100) if total_not else 0
    dp_summary.append({"date":day, "total_M":total_not/1e6, "dark_M":dark_not/1e6, "dpr":dpr, "block_dpr":blk, "carteret":cart, "block_M":block_total_not/1e6, "block_dark_M":block_dark_not/1e6})
    print(f"  {day}  total ${total_not/1e6:>7.1f}M  dark ${dark_not/1e6:>7.1f}M ({dpr:5.2f}%)  blocks ${block_total_not/1e6:>7.1f}M  block-dark% {blk:5.2f}  carteret% {cart:5.2f}")

# ---- 3. Options chain snapshot ----
print(f"\n[Options Chain Snapshot]")
sc, j = get(f"/v3/snapshot/options/{TICKER}", {"limit": 250, "order":"asc", "sort":"strike_price"})
contracts = j.get("results", [])
print(f"  Returned {len(contracts)} contracts in first page")
# Get underlying price
under = None
if contracts:
    under = contracts[0].get("underlying_asset", {}).get("price")
    if under:
        print(f"  Underlying price (live): ${under:.2f}")

# Aggregate by expiration
by_exp = defaultdict(lambda: {"call_oi":0,"put_oi":0,"call_vol":0,"put_vol":0,"call_prem":0.0,"put_prem":0.0,"contracts":0})
for c in contracts:
    det = c.get("details",{})
    exp = det.get("expiration_date")
    typ = det.get("contract_type")
    day_data = c.get("day",{}) or {}
    vol = day_data.get("volume",0) or 0
    oi = c.get("open_interest",0) or 0
    last_price = day_data.get("close") or day_data.get("vwap") or 0
    prem = vol * last_price * 100  # premium $ traded
    rec = by_exp[exp]
    rec["contracts"] += 1
    if typ == "call":
        rec["call_oi"] += oi
        rec["call_vol"] += vol
        rec["call_prem"] += prem
    elif typ == "put":
        rec["put_oi"] += oi
        rec["put_vol"] += vol
        rec["put_prem"] += prem

print(f"\n  Top expirations by volume:")
ranked = sorted(by_exp.items(), key=lambda kv: -(kv[1]["call_vol"]+kv[1]["put_vol"]))[:6]
print(f"  {'EXP':<12} {'C_VOL':>8} {'P_VOL':>8} {'P/C_VOL':>8} {'C_OI':>10} {'P_OI':>10} {'P/C_OI':>8} {'C_PREM$':>12} {'P_PREM$':>12}")
for exp, r in ranked:
    pcv = (r["put_vol"]/r["call_vol"]) if r["call_vol"] else 0
    pco = (r["put_oi"]/r["call_oi"]) if r["call_oi"] else 0
    print(f"  {exp:<12} {r['call_vol']:>8,} {r['put_vol']:>8,} {pcv:>8.2f} {r['call_oi']:>10,} {r['put_oi']:>10,} {pco:>8.2f} {r['call_prem']:>12,.0f} {r['put_prem']:>12,.0f}")

# Overall totals across page
totC_oi = sum(r["call_oi"] for r in by_exp.values())
totP_oi = sum(r["put_oi"] for r in by_exp.values())
totC_v  = sum(r["call_vol"] for r in by_exp.values())
totP_v  = sum(r["put_vol"] for r in by_exp.values())
totC_p  = sum(r["call_prem"] for r in by_exp.values())
totP_p  = sum(r["put_prem"] for r in by_exp.values())
print(f"\n  TOTAL (this page)")
print(f"  Call OI: {totC_oi:,}   Put OI: {totP_oi:,}   P/C OI ratio: {(totP_oi/totC_oi) if totC_oi else 0:.3f}")
print(f"  Call Vol: {totC_v:,}   Put Vol: {totP_v:,}   P/C Vol ratio: {(totP_v/totC_v) if totC_v else 0:.3f}")
print(f"  Call premium $: {totC_p:,.0f}   Put premium $: {totP_p:,.0f}")
print(f"  Net premium: ${(totC_p-totP_p):,.0f}  (positive = call-skewed money flow)")

# ---- 4. Short interest most recent ----
print(f"\n[Short Interest]")
sc, j = get(f"/stocks/v1/short-interest", {"ticker":TICKER, "limit":3, "order":"desc", "sort":"settlement_date"})
for row in j.get("results", []):
    print(f"  Settle {row.get('settlement_date')}  ShortInt {row.get('short_interest',0):>12,}  AvgDailyVol {row.get('avg_daily_volume',0):>12,}  DTC {row.get('days_to_cover',0):.2f}")
