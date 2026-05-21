"""
Audit all 5%+ ghost wicks for TSLA, AMD, QQQ and check whether the extreme
print was a TRF-exempt odd-lot (the suspected false-signal fingerprint).

For each wick, pull the trades in the wick minute and find which trade(s)
hit the extreme. Tag the wick as "TRF_NOISE" if the extreme trade had
exchange=4 AND condition 37 AND condition 41 AND size < 100.

Then bucket hit rates by:
  - Touched? × NOISE? (2x2 confusion)
to see if our TRF-exempt filter improves the 5%+ hit rate.
"""
import os, requests, csv, time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})
ET = timezone(timedelta(hours=-4))

TICKERS = ["TSLA", "AMD", "QQQ"]
MIN_PCT = 5.0

def get_minute_trades(ticker, dt_iso):
    # dt_iso example: 2026-04-20T14:31:00-04:00. Window: this minute only.
    d = datetime.fromisoformat(dt_iso)
    start = d
    end = d + timedelta(minutes=1)
    u = f"{BASE}/v3/trades/{ticker}"
    p = {"timestamp.gte": int(start.timestamp() * 1e9),
         "timestamp.lt":  int(end.timestamp() * 1e9),
         "limit": 50000, "order": "asc"}
    trades = []
    pages = 0
    while u and pages < 20:
        for attempt in range(5):
            try:
                r = S.get(u, params=p if pages == 0 else None, timeout=120); break
            except Exception:
                time.sleep(1 + attempt)
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}"); break
        j = r.json()
        for t in j.get("results", []):
            ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
            if not ts_ns: continue
            ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(ET)
            conds = t.get("conditions") or []
            trades.append({
                "ts": ts, "price": t["price"], "size": t.get("size", 0),
                "exchange": t.get("exchange"), "conditions": conds,
                "trf_id": t.get("trf_id"),
            })
        u = j.get("next_url"); p = None; pages += 1
    return trades

def is_trf_noise(t):
    """Trade-through-exempt odd-lot fingerprint."""
    if t["exchange"] != 4: return False
    if 37 not in t["conditions"]: return False
    if 41 not in t["conditions"]: return False
    if t["size"] >= 100: return False
    return True

results = []
print(f"{'Ticker':6} {'Date':12} {'Time':6} {'Dir':5} {'Extreme':>10} {'Wick%':>6} {'Hit':>4}  {'ExtSize':>7}  Fingerprint")
print("-"*120)

for ticker in TICKERS:
    csv_path = f"data/ghost_wicks_v2_{ticker}_trade.csv"
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if float(r["wick_pct"]) >= MIN_PCT]
    print(f"\n--- {ticker}: {len(rows)} 5%+ wicks ---", flush=True)
    for r in rows:
        try:
            trades = get_minute_trades(ticker, r["datetime"])
        except Exception as e:
            print(f"  err {r['datetime']}: {e}"); continue
        # Find extreme trades
        extreme_price = float(r["extreme"])
        # Exclude mechanically problematic trades (same as scanner)
        valid = [t for t in trades if not (set(t["conditions"]) & {2,12,16,33,52,53})]
        if not valid:
            results.append({**r, "ticker": ticker, "noise": "?", "ext_size": "?", "ext_conds": "?", "ext_exch": "?"})
            continue
        if r["direction"] == "up":
            ext_trades = [t for t in valid if abs(t["price"] - extreme_price) < 0.005]
        else:
            ext_trades = [t for t in valid if abs(t["price"] - extreme_price) < 0.005]
        # If no exact match, take the most extreme valid trade
        if not ext_trades:
            if r["direction"] == "up":
                ext_trades = sorted(valid, key=lambda x: -x["price"])[:1]
            else:
                ext_trades = sorted(valid, key=lambda x: x["price"])[:1]
        # Did ANY of the extreme prints have the noise fingerprint?
        # Standard: if THE extreme trade is noise, the wick is noise
        all_noise = all(is_trf_noise(t) for t in ext_trades)
        any_clean = any(not is_trf_noise(t) for t in ext_trades)
        # If multiple trades at extreme, "noise" means ALL are noise (i.e., no clean trade reached extreme)
        is_noise = all_noise
        # Pick first ext trade for display
        et = ext_trades[0]
        fingerprint = f"ex={et['exchange']} cond={et['conditions']} sz={et['size']} trf={et.get('trf_id')}"
        flag = "NOISE" if is_noise else "CLEAN"
        hit_str = "HIT" if r["touched"] == "True" else "MISS"
        print(f"{ticker:6} {r['date']:12} {r['time_et']:6} {r['direction']:5} {extreme_price:>10.2f} {float(r['wick_pct']):>6.2f} {hit_str:>4}  {et['size']:>7}  {flag}  {fingerprint}", flush=True)
        results.append({**r, "ticker": ticker, "noise": flag,
                        "ext_size": et["size"], "ext_conds": str(et["conditions"]),
                        "ext_exch": et["exchange"], "ext_trf": et.get("trf_id")})

# Bucket summary
print("\n" + "="*60)
print(f"\n=== Per-ticker summary ===")
for ticker in TICKERS:
    rows = [r for r in results if r["ticker"] == ticker]
    n = len(rows)
    hits = sum(1 for r in rows if r["touched"] == "True")
    noise = sum(1 for r in rows if r["noise"] == "NOISE")
    clean = sum(1 for r in rows if r["noise"] == "CLEAN")
    clean_hits = sum(1 for r in rows if r["noise"] == "CLEAN" and r["touched"] == "True")
    noise_hits = sum(1 for r in rows if r["noise"] == "NOISE" and r["touched"] == "True")
    print(f"\n{ticker} (n={n}):")
    print(f"  Original hit rate: {hits}/{n} = {hits/n*100:.1f}%")
    print(f"  Noise wicks: {noise}/{n} = {noise/n*100:.1f}%")
    print(f"  Clean wicks: {clean}/{n} = {clean/n*100:.1f}%")
    if clean: print(f"  Hit rate AFTER filter (clean only): {clean_hits}/{clean} = {clean_hits/clean*100:.1f}%")
    if noise: print(f"  Hit rate of noise wicks: {noise_hits}/{noise} = {noise_hits/noise*100:.1f}%")

# Save
os.makedirs("data/audit", exist_ok=True)
with open("data/audit/5pct_fingerprint.csv", "w", newline="") as f:
    if results:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader(); w.writerows(results)
print(f"\nSaved to data/audit/5pct_fingerprint.csv")
