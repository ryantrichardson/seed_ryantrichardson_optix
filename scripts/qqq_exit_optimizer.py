"""
QQQ 1-2% ghost wick exit optimization.

Two entry scenarios scored separately:
  E1 - Intraday entry: 1-min bar exactly 60 min after the wick (or session close if wick is within last hour)
  E2 - Next-day open: open of the next regular session

Outputs (saved to data/analysis/qqq_exit/):
  per_wick.csv     - per-wick metrics (entry prices, MFE/MAE per day, peak day, indicators)
  daily_curves.csv - average MFE/MAE per forward day across all 46 wicks
  rules_eval.csv   - simulated returns under each exit rule
  rules_eval.json  - same + summary stats
"""
import os, json, time, math
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import requests
import pandas as pd
import numpy as np

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session(); S.headers.update({"Authorization": f"Bearer {API}"})
ET = timezone(timedelta(hours=-4))
EXCLUDE = {2,12,16,33,52,53}

OUT_DIR = "data/analysis/qqq_exit"
os.makedirs(OUT_DIR, exist_ok=True)

# --- Load wicks ---
DF = pd.read_csv("data/ghost_wicks_v2_QQQ_trade.csv")
WICKS = DF[(DF.wick_pct >= 1) & (DF.wick_pct < 2)].copy().reset_index(drop=True)
print(f"Loaded {len(WICKS)} QQQ 1-2% wicks")


def fetch_minute_bars(ticker, start_dt_et, end_dt_et):
    """Fetch 1-min bars from /v3/trades (used only for wick day - raw trades incl TRF)."""
    u = f"{BASE}/v3/trades/{ticker}"
    p = {"timestamp.gte": int(start_dt_et.timestamp()*1e9),
         "timestamp.lt":  int(end_dt_et.timestamp()*1e9),
         "limit": 50000, "order": "asc"}
    by_minute = defaultdict(list)
    pages = 0
    while u and pages < 400:
        for attempt in range(5):
            try: r = S.get(u, params=p if pages==0 else None, timeout=120); break
            except Exception: time.sleep(1+attempt)
        if r.status_code != 200: break
        j = r.json()
        for t in j.get("results", []):
            ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
            if not ts_ns: continue
            conds = set(t.get("conditions") or [])
            if conds & EXCLUDE: continue
            ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(ET)
            minute = ts.replace(second=0, microsecond=0)
            by_minute[minute].append(t["price"])
        u = j.get("next_url"); p = None; pages += 1
    bars = []
    for m in sorted(by_minute):
        ps = by_minute[m]
        bars.append({"t": m, "o": ps[0], "h": max(ps), "l": min(ps), "c": ps[-1], "n": len(ps)})
    return bars


def fetch_minute_aggs(ticker, start_date_str, end_date_str):
    """Fetch 1-min bars from /v2/aggs (fast, used for forward window)."""
    u = f"{BASE}/v2/aggs/ticker/{ticker}/range/1/minute/{start_date_str}/{end_date_str}"
    p = {"adjusted":"true","sort":"asc","limit":50000}
    bars = []
    pages = 0
    while u and pages < 50:
        for attempt in range(5):
            try: r = S.get(u, params=p if pages==0 else None, timeout=60); break
            except Exception: time.sleep(1+attempt)
        if r.status_code != 200: break
        j = r.json()
        for x in j.get("results", []):
            ts = datetime.fromtimestamp(x["t"]/1000, tz=timezone.utc).astimezone(ET)
            bars.append({"t": ts, "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"], "n": x.get("n",0)})
        u = j.get("next_url"); p = None; pages += 1
    return bars


def fetch_daily_bars(ticker, start_date_str, n_forward=12):
    d = datetime.strptime(start_date_str, "%Y-%m-%d")
    end = d + timedelta(days=n_forward + 25)
    u = f"{BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start_date_str}/{end.strftime('%Y-%m-%d')}"
    p = {"adjusted":"true","sort":"asc","limit":50}
    for attempt in range(5):
        try: r = S.get(u, params=p, timeout=60); break
        except Exception: time.sleep(1+attempt)
    if r.status_code != 200: return []
    bars = []
    for x in r.json().get("results", []):
        ts = datetime.fromtimestamp(x["t"]/1000, tz=timezone.utc).astimezone(ET)
        bars.append({"t": ts.date(), "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"], "v": x.get("v",0)})
    return bars


# --- Indicators ---
def rsi(values, period=14):
    if len(values) < period+1: return np.nan
    a = np.array(values, dtype=float)
    d = np.diff(a)
    up = np.maximum(d, 0); dn = np.maximum(-d, 0)
    au = np.mean(up[-period:]); ad = np.mean(dn[-period:])
    if ad == 0: return 100.0
    rs = au/ad
    return 100 - (100/(1+rs))

def stochrsi(values, period=14, smooth_k=3, smooth_d=3):
    if len(values) < period * 2: return (np.nan, np.nan)
    rsis = []
    for i in range(period, len(values)+1):
        rsis.append(rsi(values[max(0,i-period-1):i], period))
    if len(rsis) < period: return (np.nan, np.nan)
    lo = min(rsis[-period:]); hi = max(rsis[-period:])
    if hi == lo: return (50.0, 50.0)
    k_raw = [(r - lo)/(hi-lo)*100 if hi>lo else 50.0 for r in rsis[-period:]]
    k = np.mean(k_raw[-smooth_k:])
    return (k, k)  # report K (D smoothing not needed for snapshot)

def atr(highs, lows, closes, period=14):
    if len(highs) < period+1: return np.nan
    trs = []
    for i in range(1, len(highs)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    return np.mean(trs[-period:])


# --- Per-wick processing ---
results = []
daily_curve_rows = []
print("\nProcessing each wick...")

for i, w in WICKS.iterrows():
    wick_date = w['date']
    wick_dt = datetime.strptime(f"{w['date']} {w['time_et']}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
    direction = w['direction']  # 'up' = up-wick, target above; 'down' = down-wick, target below
    extreme = w['extreme']
    wick_body_close = w['close']  # body close of wick bar
    print(f"\n[{i+1}/{len(WICKS)}] {wick_date} {w['time_et']} {direction} extreme=${extreme}")

    # Need bars from wick day session start to +12 trading days forward (we'll fetch by calendar; ~17 days)
    sess_start = wick_dt.replace(hour=9, minute=30, second=0, microsecond=0)
    wick_session_end = wick_dt.replace(hour=16, minute=0, second=0, microsecond=0)
    end_calendar = wick_dt + timedelta(days=20)

    # 1) Wick-day intraday bars + pre-wick lookback for indicators
    pre_start = sess_start
    day_bars = fetch_minute_bars("QQQ", pre_start, wick_session_end)

    # 2) Forward bars: wick session end → +20 calendar days (use fast aggs)
    fwd_start_str = wick_dt.strftime("%Y-%m-%d")
    fwd_end_str = end_calendar.strftime("%Y-%m-%d")
    forward_bars_all = fetch_minute_aggs("QQQ", fwd_start_str, fwd_end_str)
    forward_bars = [b for b in forward_bars_all if b['t'] >= wick_session_end]

    # 3) Daily bars for forward window
    daily = fetch_daily_bars("QQQ", wick_date, n_forward=12)

    if not day_bars or not forward_bars or not daily:
        print("  fetch failure - skip")
        continue

    # --- ENTRY E1: 1 hour after wick, intraday ---
    e1_target_dt = wick_dt + timedelta(minutes=60)
    if e1_target_dt >= wick_session_end:
        # wick was in last hour - use session close
        e1_bar = day_bars[-1]
        e1_kind = "session_close"
    else:
        # find first bar at-or-after target minute
        candidate = next((b for b in day_bars if b['t'] >= e1_target_dt), None)
        if candidate is None:
            e1_bar = day_bars[-1]; e1_kind = "session_close"
        else:
            e1_bar = candidate; e1_kind = "1h_after"
    e1_price = e1_bar['c']
    e1_time = e1_bar['t']

    # --- ENTRY E2: next regular session open ---
    # First daily bar is wick day; second is next session
    if len(daily) < 2:
        print("  no next session - skip")
        continue
    e2_session_date = daily[1]['t']
    e2_price = daily[1]['o']

    # --- Pre-wick lookback for indicators (at wick bar minute) ---
    pre_wick_closes = [b['c'] for b in day_bars if b['t'] <= wick_dt]
    pre_wick_highs = [b['h'] for b in day_bars if b['t'] <= wick_dt]
    pre_wick_lows = [b['l'] for b in day_bars if b['t'] <= wick_dt]
    rsi_1m = rsi(pre_wick_closes, 14) if len(pre_wick_closes) >= 15 else np.nan
    srsi_1m, _ = stochrsi(pre_wick_closes, 14) if len(pre_wick_closes) >= 28 else (np.nan, np.nan)
    atr_1m = atr(pre_wick_highs, pre_wick_lows, pre_wick_closes, 14) if len(pre_wick_closes) >= 15 else np.nan

    # 5-min RSI: aggregate 1-min to 5-min
    if len(pre_wick_closes) >= 75:
        bars5 = []
        # group by 5-min buckets
        groups = defaultdict(list)
        for b in day_bars:
            if b['t'] > wick_dt: break
            bucket = b['t'].replace(minute=(b['t'].minute // 5)*5)
            groups[bucket].append(b)
        for bucket in sorted(groups):
            bs = groups[bucket]
            bars5.append({"c": bs[-1]['c'], "h": max(x['h'] for x in bs), "l": min(x['l'] for x in bs)})
        closes5 = [x['c'] for x in bars5]
        rsi_5m = rsi(closes5, 14) if len(closes5) >= 15 else np.nan
    else:
        rsi_5m = np.nan

    # VWAP from session start to wick
    vwap_num = 0; vwap_den = 0
    for b in day_bars:
        if b['t'] > wick_dt: break
        tp = (b['h'] + b['l'] + b['c']) / 3
        vol = b.get('n', 1)  # proxy: tick count
        vwap_num += tp * vol; vwap_den += vol
    vwap = vwap_num / vwap_den if vwap_den else np.nan
    dist_vwap = ((wick_body_close - vwap) / vwap * 100) if vwap and not np.isnan(vwap) else np.nan

    # Wick / ATR ratio
    wick_dollar = abs(extreme - wick_body_close)
    wick_atr_ratio = (wick_dollar / atr_1m) if atr_1m and not np.isnan(atr_1m) and atr_1m > 0 else np.nan

    # --- Forward MFE/MAE per trading day ---
    # Build a flat list of bars: starting after wick session close, group by trading date
    # Use daily highs/lows for MFE/MAE per day (daily bars[1..11] are days 1..10)
    # daily[0] = wick day (already done); daily[1..10] = forward days 1..10
    fwd_days = daily[1:11]  # up to 10
    if len(fwd_days) < 10:
        print(f"  only {len(fwd_days)} forward days available")

    # Direction sign for "favorable"
    sign = 1 if direction == "up" else -1  # up wick: favorable = price up
    target_dist = (extreme - wick_body_close) * sign  # positive number

    for entry_label, entry_price in [("E1", e1_price), ("E2", e2_price)]:
        # Cumulative MFE/MAE
        running_mfe = 0.0; running_mfe_day = 0
        running_mae = 0.0
        per_day_mfe = []; per_day_mae = []
        touched_target = False; touched_day = None
        # For E1, day 0 = wick session (afternoon only after entry); for E2, day 0 = next session
        # Simplification: use daily bars from the appropriate start
        if entry_label == "E1":
            # day 0 = remainder of wick session (post-entry hi/lo via forward_bars)
            d0_post = [b for b in day_bars if b['t'] >= e1_time and b['t'] < wick_session_end] + \
                      [b for b in forward_bars if b['t'].date() == wick_dt.date()]
            d0_h = max((b['h'] for b in d0_post), default=entry_price)
            d0_l = min((b['l'] for b in d0_post), default=entry_price)
            day0_mfe = (d0_h - entry_price) * sign if sign==1 else (entry_price - d0_l)
            day0_mae = (entry_price - d0_l) * (-1 if sign==1 else 1) * -1  # adverse = price moves against direction
            # cleaner: MFE = max favorable; MAE = min favorable (i.e. worst against)
            day0_mfe = max((b['h']*sign + b['l']*(-sign) for b in []), default=0)  # placeholder
            # Re-derive simply:
            if sign == 1:
                day0_mfe = d0_h - entry_price
                day0_mae = entry_price - d0_l
            else:
                day0_mfe = entry_price - d0_l
                day0_mae = d0_h - entry_price
            day0_target_hit = (sign==1 and d0_h >= extreme) or (sign==-1 and d0_l <= extreme)
            per_day_mfe.append(day0_mfe); per_day_mae.append(day0_mae)
            if day0_target_hit and not touched_target:
                touched_target = True; touched_day = 0
            running_mfe = day0_mfe; running_mae = day0_mae
            forward_days_to_eval = fwd_days  # days 1..10
            day_offset = 1
        else:
            # E2: enter at next session open. Forward days indexed 0=next session, 1..9 after
            forward_days_to_eval = fwd_days
            day_offset = 0

        for di, d in enumerate(forward_days_to_eval):
            day_idx = di + day_offset
            h, l = d['h'], d['l']
            if sign == 1:
                d_mfe = h - entry_price
                d_mae = entry_price - l
                hit = (h >= extreme)
            else:
                d_mfe = entry_price - l
                d_mae = h - entry_price
                hit = (l <= extreme)
            per_day_mfe.append(d_mfe); per_day_mae.append(d_mae)
            if d_mfe > running_mfe:
                running_mfe = d_mfe; running_mfe_day = day_idx
            if d_mae > running_mae:
                running_mae = d_mae
            if hit and not touched_target:
                touched_target = True; touched_day = day_idx

        # Save per-day MFE/MAE to daily_curve_rows
        for di, (mfe, mae) in enumerate(zip(per_day_mfe, per_day_mae)):
            daily_curve_rows.append({
                "wick_idx": i, "entry": entry_label, "day": di + (0 if entry_label=="E1" else 0),
                "mfe_pct": mfe / entry_price * 100,
                "mae_pct": mae / entry_price * 100,
                "direction": direction,
            })

        results.append({
            "wick_idx": i, "date": wick_date, "time_et": w['time_et'], "direction": direction,
            "wick_pct": w['wick_pct'], "extreme": extreme, "wick_close": wick_body_close,
            "entry": entry_label, "entry_price": entry_price, "entry_kind": e1_kind if entry_label=="E1" else "next_open",
            "target_dist_dollar": target_dist, "target_dist_pct": target_dist / entry_price * 100,
            "touched_target": touched_target, "touched_day": touched_day,
            "peak_mfe_dollar": running_mfe, "peak_mfe_pct": running_mfe / entry_price * 100, "peak_mfe_day": running_mfe_day,
            "peak_mae_dollar": running_mae, "peak_mae_pct": running_mae / entry_price * 100,
            "rsi_1m_at_wick": rsi_1m, "stochrsi_1m_at_wick": srsi_1m, "rsi_5m_at_wick": rsi_5m,
            "atr_1m_at_wick": atr_1m, "wick_atr_ratio": wick_atr_ratio,
            "vwap_at_wick": vwap, "dist_vwap_pct_at_wick": dist_vwap,
        })

# --- Save outputs ---
per_wick_df = pd.DataFrame(results)
per_wick_df.to_csv(f"{OUT_DIR}/per_wick.csv", index=False)
print(f"\nSaved {OUT_DIR}/per_wick.csv ({len(per_wick_df)} rows = {len(per_wick_df)//2} wicks × 2 entries)")

curves_df = pd.DataFrame(daily_curve_rows)
curves_df.to_csv(f"{OUT_DIR}/daily_curves.csv", index=False)
print(f"Saved {OUT_DIR}/daily_curves.csv")

# --- Aggregate analyses ---
print("\n=== A: Forward profit curve ===")
for entry in ["E1", "E2"]:
    sub = curves_df[curves_df.entry == entry]
    if sub.empty: continue
    agg = sub.groupby("day").agg(mfe_mean=("mfe_pct","mean"), mfe_median=("mfe_pct","median"),
                                  mae_mean=("mae_pct","mean"), n=("mfe_pct","count"))
    print(f"\n{entry}:")
    print(agg.to_string())

# --- Exit rule simulation ---
print("\n=== Exit rule simulation ===")
rule_rows = []
for entry in ["E1", "E2"]:
    sub = per_wick_df[per_wick_df.entry == entry].copy()
    if sub.empty: continue

    rules = {
        "TP_at_wick_target": lambda r: r['target_dist_pct'] if r['touched_target'] else -r['peak_mae_pct'],  # win = target_dist, lose = -mae
        "TP_at_1.5x_target": None,
        "TP_at_2.0x_target": None,
        "Time_exit_d3": None,
        "Time_exit_d5": None,
        "Time_exit_d10": None,
        "Trail_1pct_from_peak": None,
        "Hold_to_d10_no_TP": None,
    }
    # Implement using daily curves
    cur = curves_df[curves_df.entry == entry]

    for rule_name in rules:
        rets = []
        for wi, w_rows in cur.groupby("wick_idx"):
            row = sub[sub.wick_idx == wi].iloc[0]
            entry_price = row['entry_price']
            target_dist_pct = row['target_dist_pct']
            touched = row['touched_target']
            touched_day = row['touched_day']
            mfe_path = w_rows.sort_values("day")['mfe_pct'].tolist()
            mae_path = w_rows.sort_values("day")['mae_pct'].tolist()

            if rule_name == "TP_at_wick_target":
                if touched:
                    rets.append(target_dist_pct)
                else:
                    # exit at end of horizon at the close of d10 (approximation: use mfe_path[-1] - assumes no SL)
                    # better: close-out at the last day's close; use peak_mae as drawdown floor
                    # We'll use d10 close approximation: end at d10 MFE point (which can be 0 or negative)
                    # Simplification: if never hit, take the d10 close return -> approximated as 0 for now
                    rets.append(-mae_path[-1])  # worst case approximated
            elif rule_name in ("TP_at_1.5x_target", "TP_at_2.0x_target"):
                mult = 1.5 if "1.5" in rule_name else 2.0
                target_pct = target_dist_pct * mult
                hit_day = None
                for d, mfe in enumerate(mfe_path):
                    if mfe >= target_pct:
                        hit_day = d; break
                if hit_day is not None:
                    rets.append(target_pct)
                else:
                    rets.append(-mae_path[-1])
            elif rule_name.startswith("Time_exit_d"):
                d_exit = int(rule_name.split("d")[1])
                idx = min(d_exit, len(mfe_path)-1)
                # exit return = mfe at that day if direction-aligned and price still favorable, else MAE
                # Simplification: take mfe_path[idx] - mae_path[idx] as net (price at the close that day)
                # Cleaner: use mfe and mae path arithmetic. Approx daily close return ~ mfe - mae of that day's range
                # For now: use 0.5*(mfe-mae) as a centroid estimate
                rets.append(0.5 * (mfe_path[idx] - mae_path[idx]))
            elif rule_name == "Trail_1pct_from_peak":
                # walk daily; track running peak MFE; exit when pullback from peak >= 1%
                # since we only have daily granularity, this is approximate
                peak = 0.0; exit_ret = None
                for d, mfe in enumerate(mfe_path):
                    if mfe > peak: peak = mfe
                    # pullback: mae - we use mae[d] - (peak - mfe[d]) as a proxy
                    pullback = peak - mfe  # how much we've given back from peak by end of day d
                    if pullback >= 1.0:
                        exit_ret = peak - 1.0
                        break
                if exit_ret is None:
                    exit_ret = mfe_path[-1] if mfe_path else 0
                rets.append(exit_ret)
            elif rule_name == "Hold_to_d10_no_TP":
                rets.append(0.5 * (mfe_path[-1] - mae_path[-1]))

        if rets:
            avg = np.mean(rets); med = np.median(rets); win_rate = sum(1 for x in rets if x>0)/len(rets)*100
            rule_rows.append({"entry": entry, "rule": rule_name, "n": len(rets),
                              "avg_ret_pct": round(avg,3), "median_ret_pct": round(med,3),
                              "win_rate_pct": round(win_rate,1)})

rules_df = pd.DataFrame(rule_rows)
rules_df.to_csv(f"{OUT_DIR}/rules_eval.csv", index=False)
print(rules_df.to_string(index=False))

# --- Indicator correlation ---
print("\n=== B: Indicator correlation with peak_mfe_pct ===")
for entry in ["E1", "E2"]:
    sub = per_wick_df[per_wick_df.entry == entry]
    if sub.empty: continue
    print(f"\n{entry}:")
    for col in ["rsi_1m_at_wick", "stochrsi_1m_at_wick", "rsi_5m_at_wick", "wick_atr_ratio", "dist_vwap_pct_at_wick"]:
        s = sub[[col, "peak_mfe_pct"]].dropna()
        if len(s) < 5: continue
        corr = s[col].corr(s.peak_mfe_pct)
        # also corr with touched
        s2 = sub[[col, "touched_target"]].dropna()
        corr_hit = s2[col].astype(float).corr(s2.touched_target.astype(float))
        print(f"  {col:30s}  n={len(s):3d}  corr(peak_mfe)={corr:+.3f}  corr(hit)={corr_hit:+.3f}")

# --- Final summary JSON ---
summary = {
    "n_wicks": int(len(WICKS)),
    "rules": rules_df.to_dict(orient="records"),
    "indicator_corr_E2": {},
}
with open(f"{OUT_DIR}/summary.json","w") as f:
    json.dump(summary, f, indent=2, default=str)
print(f"\nSaved {OUT_DIR}/summary.json")
print("\nDONE.")
