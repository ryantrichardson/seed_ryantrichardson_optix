"""Full SLV options chain — paginated, with strike-by-strike + expiration breakdown."""
import os, requests, time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})
TICKER = "PLTR"

def getj(path, params=None):
    for attempt in range(5):
        try:
            r = S.get(f"{BASE}{path}", params=params, timeout=120)
            if r.status_code == 429:
                time.sleep(3 + attempt*2); continue
            return r.status_code, (r.json() if r.headers.get("content-type","").startswith("application/json") else {})
        except Exception as e:
            if attempt == 4: raise
            time.sleep(1 + attempt)

print(f"=== {TICKER} multi-signal read @ {datetime.now(timezone.utc).isoformat()} ===\n")

# ---- A. Price/Volume ----
end = datetime.now(timezone.utc).date()
start = end - timedelta(days=30)
sc, j = getj(f"/v2/aggs/ticker/{TICKER}/range/1/day/{start}/{end}", {"adjusted":"true","sort":"asc"})
bars = j.get("results", [])
print(f"[Price/Volume] last {len(bars)} daily bars (showing last 10)")
for b in bars[-10:]:
    d = datetime.fromtimestamp(b['t']/1000, timezone.utc).date()
    print(f"  {d}  O={b['o']:.2f}  H={b['h']:.2f}  L={b['l']:.2f}  C={b['c']:.2f}  V={b['v']:>13,.0f}  VWAP={b.get('vw',0):.2f}")
if len(bars) >= 2:
    chg = (bars[-1]['c']/bars[-2]['c']-1)*100
    chg5 = (bars[-1]['c']/bars[-6]['c']-1)*100 if len(bars)>=6 else None
    chg20 = (bars[-1]['c']/bars[0]['c']-1)*100
    avg_vol = sum(b['v'] for b in bars[-20:]) / min(20, len(bars))
    last_vol_ratio = bars[-1]['v'] / avg_vol if avg_vol else 0
    print(f"\n  1d change: {chg:+.2f}%   5d change: {chg5:+.2f}%   20d change: {chg20:+.2f}%")
    print(f"  Last volume vs 20d avg: {last_vol_ratio:.2f}x")

# ---- B. Dark Pool last 5 days ----
print(f"\n[Dark Pool Trades] last 5 days")
from datetime import timedelta as _td
def trading_days_back(n):
    out, d = [], datetime.now(timezone.utc).date() - _td(days=1)
    while len(out) < n:
        if d.weekday() < 5: out.append(d)
        d -= _td(days=1)
    return list(reversed(out))

for day in trading_days_back(5):
    start_ns = int(datetime.combine(day, datetime.min.time(), timezone.utc).timestamp()*1e9)
    end_ns = int(datetime.combine(day+_td(days=1), datetime.min.time(), timezone.utc).timestamp()*1e9)
    u = f"{BASE}/v3/trades/{TICKER}"
    p = {"timestamp.gte":start_ns, "timestamp.lt":end_ns, "limit":50000, "order":"asc"}
    total_n = dark_n = block_dark = block_tot = 0.0
    venue_n = {201:0.0, 202:0.0, 203:0.0}
    pages_t = 0
    while u and pages_t < 40:
        attempt = 0; resp=None
        while attempt < 5:
            try:
                resp = S.get(u, params=p if pages_t==0 else None, timeout=120); break
            except Exception:
                attempt += 1; time.sleep(1+attempt)
        if resp is None or resp.status_code != 200:
            print(f"  ! {day} page {pages_t} failed"); break
        jj = resp.json()
        for tr in jj.get("results", []):
            sz = tr.get("size") or 0; pr = tr.get("price") or 0
            nn = sz*pr
            if nn == 0: continue
            total_n += nn
            is_dark = tr.get("exchange")==4 and tr.get("trf_id") is not None
            if is_dark:
                dark_n += nn
                if tr.get("trf_id") in venue_n: venue_n[tr.get("trf_id")] += nn
            if nn >= 100_000:
                block_tot += nn
                if is_dark: block_dark += nn
        u = jj.get("next_url"); p = None; pages_t += 1
    dpr = dark_n/total_n*100 if total_n else 0
    blk = block_dark/block_tot*100 if block_tot else 0
    cart = venue_n[202]/total_n*100 if total_n else 0
    print(f"  {day}  total ${total_n/1e6:>7.1f}M  dark ${dark_n/1e6:>7.1f}M ({dpr:5.2f}%)  blocks ${block_tot/1e6:>7.1f}M  block-dark% {blk:5.2f}  carteret% {cart:5.2f}")

print(f"\n=== {TICKER} FULL options chain ===\n")

# Paginate the full chain
url = f"{BASE}/v3/snapshot/options/{TICKER}"
params = {"limit": 250, "order": "asc", "sort": "expiration_date"}
all_contracts = []
pages = 0
underlying_price = None
while url and pages < 200:
    last_err = None
    j = None
    for attempt in range(5):
        try:
            r = S.get(url, params=params if pages == 0 else None, timeout=120)
            if r.status_code == 429:
                time.sleep(3 + attempt*2); continue
            if r.status_code != 200:
                print(f"  ! page {pages} HTTP {r.status_code}: {r.text[:200]}"); j = None; break
            j = r.json(); break
        except Exception as e:
            last_err = e
            time.sleep(1 + attempt)
    if j is None:
        print(f"  ! page {pages} gave up after retries (last: {last_err})")
        break
    results = j.get("results", [])
    all_contracts.extend(results)
    if results and underlying_price is None:
        underlying_price = results[0].get("underlying_asset", {}).get("price")
    url = j.get("next_url")
    params = None
    pages += 1

print(f"Pulled {len(all_contracts):,} contracts across {pages} pages")
print(f"Underlying live price: ${underlying_price:.2f}\n" if underlying_price else "")

# Aggregate
by_exp = defaultdict(lambda: {"call_oi":0,"put_oi":0,"call_vol":0,"put_vol":0,"call_prem":0.0,"put_prem":0.0,"contracts":0})
by_strike = defaultdict(lambda: {"call_oi":0,"put_oi":0,"call_vol":0,"put_vol":0,"call_prem":0.0,"put_prem":0.0})
overall = {"call_oi":0,"put_oi":0,"call_vol":0,"put_vol":0,"call_prem":0.0,"put_prem":0.0}
ivs_call, ivs_put = [], []

for c in all_contracts:
    det = c.get("details", {})
    exp = det.get("expiration_date")
    typ = det.get("contract_type")
    strike = det.get("strike_price")
    day = c.get("day", {}) or {}
    vol = day.get("volume", 0) or 0
    oi = c.get("open_interest", 0) or 0
    last = day.get("close") or day.get("vwap") or 0
    prem = vol * last * 100
    iv = c.get("implied_volatility")
    e = by_exp[exp]
    s = by_strike[strike] if strike else None
    e["contracts"] += 1
    if typ == "call":
        e["call_oi"] += oi; e["call_vol"] += vol; e["call_prem"] += prem
        overall["call_oi"] += oi; overall["call_vol"] += vol; overall["call_prem"] += prem
        if s is not None:
            s["call_oi"] += oi; s["call_vol"] += vol; s["call_prem"] += prem
        if iv: ivs_call.append((strike, exp, iv))
    elif typ == "put":
        e["put_oi"] += oi; e["put_vol"] += vol; e["put_prem"] += prem
        overall["put_oi"] += oi; overall["put_vol"] += vol; overall["put_prem"] += prem
        if s is not None:
            s["put_oi"] += oi; s["put_vol"] += vol; s["put_prem"] += prem
        if iv: ivs_put.append((strike, exp, iv))

# ---- 1. Overall ----
print("=== OVERALL (full chain) ===")
print(f"  Call OI:      {overall['call_oi']:>12,}")
print(f"  Put OI:       {overall['put_oi']:>12,}")
print(f"  P/C OI:       {(overall['put_oi']/overall['call_oi']) if overall['call_oi'] else 0:>12.3f}")
print(f"  Call Vol:     {overall['call_vol']:>12,}")
print(f"  Put Vol:      {overall['put_vol']:>12,}")
print(f"  P/C Vol:      {(overall['put_vol']/overall['call_vol']) if overall['call_vol'] else 0:>12.3f}")
print(f"  Call $ prem:  ${overall['call_prem']:>15,.0f}")
print(f"  Put $ prem:   ${overall['put_prem']:>15,.0f}")
print(f"  Net premium:  ${(overall['call_prem']-overall['put_prem']):>15,.0f}  (positive = call-skewed)")
prem_total = overall["call_prem"] + overall["put_prem"]
if prem_total:
    print(f"  Call share of $: {overall['call_prem']/prem_total*100:.1f}%   Put share of $: {overall['put_prem']/prem_total*100:.1f}%")

# ---- 2. By expiration, ranked by total dollar premium ----
print("\n=== TOP EXPIRATIONS BY DOLLAR PREMIUM ===")
ranked = sorted(by_exp.items(), key=lambda kv: -(kv[1]["call_prem"]+kv[1]["put_prem"]))[:12]
print(f"  {'EXP':<12} {'C_VOL':>8} {'P_VOL':>8} {'P/C_V':>7} {'C_OI':>10} {'P_OI':>10} {'P/C_OI':>7} {'C_PREM$':>14} {'P_PREM$':>14} {'NET$':>14}")
for exp, r in ranked:
    pcv = (r["put_vol"]/r["call_vol"]) if r["call_vol"] else 0
    pco = (r["put_oi"]/r["call_oi"]) if r["call_oi"] else 0
    net = r["call_prem"]-r["put_prem"]
    print(f"  {exp:<12} {r['call_vol']:>8,} {r['put_vol']:>8,} {pcv:>7.2f} {r['call_oi']:>10,} {r['put_oi']:>10,} {pco:>7.2f} {r['call_prem']:>14,.0f} {r['put_prem']:>14,.0f} {net:>+14,.0f}")

# ---- 3. Top strikes (concentration) ----
print("\n=== TOP 15 CALL STRIKES BY OI ===")
top_calls = sorted(by_strike.items(), key=lambda kv: -kv[1]["call_oi"])[:15]
print(f"  {'STRIKE':>8} {'C_OI':>10} {'C_VOL':>8} {'C_PREM$':>14}")
for k, r in top_calls:
    if r["call_oi"] == 0: continue
    moneyness = ""
    if underlying_price:
        diff = (k - underlying_price)/underlying_price*100
        moneyness = f"({diff:+.1f}%)"
    print(f"  {k:>8.2f} {r['call_oi']:>10,} {r['call_vol']:>8,} {r['call_prem']:>14,.0f}  {moneyness}")

print("\n=== TOP 15 PUT STRIKES BY OI ===")
top_puts = sorted(by_strike.items(), key=lambda kv: -kv[1]["put_oi"])[:15]
print(f"  {'STRIKE':>8} {'P_OI':>10} {'P_VOL':>8} {'P_PREM$':>14}")
for k, r in top_puts:
    if r["put_oi"] == 0: continue
    moneyness = ""
    if underlying_price:
        diff = (k - underlying_price)/underlying_price*100
        moneyness = f"({diff:+.1f}%)"
    print(f"  {k:>8.2f} {r['put_oi']:>10,} {r['put_vol']:>8,} {r['put_prem']:>14,.0f}  {moneyness}")

# ---- 4. Today's largest premium trades ----
print("\n=== TOP 15 CONTRACTS BY $ PREMIUM TRADED TODAY ===")
ranked_contracts = []
for c in all_contracts:
    det = c.get("details", {})
    day = c.get("day", {}) or {}
    vol = day.get("volume", 0) or 0
    last = day.get("close") or day.get("vwap") or 0
    prem = vol * last * 100
    if prem > 0:
        ranked_contracts.append({
            "type": det.get("contract_type"),
            "strike": det.get("strike_price"),
            "exp": det.get("expiration_date"),
            "vol": vol, "oi": c.get("open_interest",0) or 0,
            "last": last, "prem": prem,
            "iv": c.get("implied_volatility")
        })
ranked_contracts.sort(key=lambda x: -x["prem"])
print(f"  {'TYPE':>5} {'STRIKE':>8} {'EXP':<12} {'VOL':>8} {'OI':>10} {'LAST':>8} {'$PREM':>14} {'IV':>6}")
for c in ranked_contracts[:15]:
    iv = f"{c['iv']*100:.1f}%" if c['iv'] else "-"
    print(f"  {c['type']:>5} {c['strike']:>8.2f} {c['exp']:<12} {c['vol']:>8,} {c['oi']:>10,} {c['last']:>8.2f} {c['prem']:>14,.0f} {iv:>6}")

# ---- 5. Volume/OI ratio - new positioning today ----
print("\n=== HIGH VOLUME / OI RATIO (new positioning today, vol>=100) ===")
fresh = []
for c in all_contracts:
    det = c.get("details", {})
    day = c.get("day", {}) or {}
    vol = day.get("volume", 0) or 0
    oi = c.get("open_interest", 0) or 0
    if vol >= 100 and oi > 0:
        ratio = vol / oi
        fresh.append({
            "type": det.get("contract_type"),
            "strike": det.get("strike_price"),
            "exp": det.get("expiration_date"),
            "vol": vol, "oi": oi, "ratio": ratio,
            "prem": vol * (day.get("close") or day.get("vwap") or 0) * 100
        })
fresh.sort(key=lambda x: -x["ratio"])
print(f"  {'TYPE':>5} {'STRIKE':>8} {'EXP':<12} {'VOL':>8} {'OI':>10} {'V/OI':>6} {'$PREM':>14}")
for c in fresh[:15]:
    print(f"  {c['type']:>5} {c['strike']:>8.2f} {c['exp']:<12} {c['vol']:>8,} {c['oi']:>10,} {c['ratio']:>6.2f} {c['prem']:>14,.0f}")

# ---- 6. Near-term IV skew (closest expiration with enough data) ----
print("\n=== IV SKEW (front-month) ===")
near_exp = None
for exp, r in sorted(by_exp.items()):
    if exp and r["call_vol"]+r["put_vol"] > 50:
        near_exp = exp
        break
if near_exp:
    print(f"  Front-month w/ flow: {near_exp}")
    front_calls = [(s,e,iv) for s,e,iv in ivs_call if e == near_exp]
    front_puts = [(s,e,iv) for s,e,iv in ivs_put if e == near_exp]
    front_calls.sort(); front_puts.sort()
    print(f"  CALL strikes (strike → IV):")
    for s,e,iv in front_calls[:15]:
        marker = " <-- ATM" if underlying_price and abs(s-underlying_price) < 1 else ""
        print(f"    {s:>7.2f}  IV {iv*100:>6.2f}%{marker}")
    print(f"  PUT strikes (strike → IV):")
    for s,e,iv in front_puts[:15]:
        marker = " <-- ATM" if underlying_price and abs(s-underlying_price) < 1 else ""
        print(f"    {s:>7.2f}  IV {iv*100:>6.2f}%{marker}")
