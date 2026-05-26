"""
For the two QQQ pre-market wicks on 11/20/25 (7:00 and 8:15 ET), pull:
  A) Massive's own 1-min OHLC for the candle minute (do they see the wick?)
  B) /v3/trades in the 5-min candle window  - any prints at extreme?
  C) /v3/trades over the next 10 trading days, searching for the FIRST real print
     at or beyond the wick extreme. Verify by reading the actual trade tape.
  D) Around the verified hit minute: 5-min volume, DPR, conditions of fills at the
     wick price.

No more 'minute bar high reached the level' phantom hits.
"""
import os, json, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
ET = timezone(timedelta(hours=-5))  # EST in November
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

WICKS = json.loads(Path("data/qqq_2_wicks.json").read_text())["wicks"]


def fetch_trades(ticker, s_ns, e_ns):
    url = f"{BASE}/v3/trades/{ticker}"
    params = {"timestamp.gte": s_ns, "timestamp.lt": e_ns, "limit": 50000, "order": "asc"}
    out = []
    pages = 0
    while url and pages < 50:
        r = S.get(url, params=params if pages == 0 else None, timeout=90)
        if r.status_code != 200:
            print(f"  ! HTTP {r.status_code}: {r.text[:200]}")
            break
        j = r.json()
        out.extend(j.get("results", []))
        url = j.get("next_url")
        params = None
        pages += 1
    return out


def fetch_minute_bars(ticker, s_date, e_date):
    r = S.get(f"{BASE}/v2/aggs/ticker/{ticker}/range/1/minute/{s_date}/{e_date}",
              params={"adjusted": "true", "sort": "asc", "limit": 50000}, timeout=60)
    all_bars = []
    if r.status_code != 200:
        return all_bars
    j = r.json()
    all_bars.extend(j.get("results", []))
    while j.get("next_url"):
        url = j["next_url"] + f"&apiKey={API}"
        r = requests.get(url, timeout=60)
        if r.status_code != 200:
            break
        j = r.json()
        all_bars.extend(j.get("results", []))
    return all_bars


def analyze_5min_anatomy(trades, direction, extreme):
    if not trades:
        return {}
    total_sz = sum(t.get("size") or 0 for t in trades)
    total_not = sum((t.get("size") or 0) * (t.get("price") or 0) for t in trades)
    dark_sz = sum((t.get("size") or 0) for t in trades if t.get("exchange") == 4 and t.get("trf_id") is not None)
    dark_not = sum((t.get("size") or 0) * (t.get("price") or 0) for t in trades if t.get("exchange") == 4 and t.get("trf_id") is not None)
    prices = [t.get("price") for t in trades if t.get("price")]
    lo, hi = (min(prices), max(prices)) if prices else (None, None)
    # Prints at-or-beyond extreme (tolerance 0.05)
    if direction == "down":
        ext = [t for t in trades if (t.get("price") or 1e9) <= extreme + 0.05]
    else:
        ext = [t for t in trades if (t.get("price") or 0) >= extreme - 0.05]
    return {
        "trades": len(trades), "total_size": total_sz, "total_notional_M": round(total_not / 1e6, 3),
        "dpr_pct": round(dark_not / total_not * 100, 2) if total_not else 0,
        "actual_low": lo, "actual_high": hi,
        "prints_at_extreme": len(ext),
        "prints_at_extreme_size": sum(t.get("size") or 0 for t in ext),
    }


def main():
    for w in WICKS:
        wid = w["id"]
        ticker = w["ticker"]
        direction = w["direction"]
        extreme = float(w["extreme"])
        dt_et = datetime.strptime(f"{w['date']} {w['time_et']}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)

        print(f"\n{'='*90}")
        print(f"WICK {wid}: {ticker} {w['date']} {w['time_et']} ET  dir={direction}  extreme={extreme}")
        print(f"          screenshot OHLC: O={w['open']} H={w['high']} L={w['low']} C={w['close']}")
        print(f"{'='*90}")

        # --- A) Massive 1-min OHLC for the wick minute ---
        bars = fetch_minute_bars(ticker, w["date"], w["date"])
        target_ms = int(dt_et.timestamp() * 1000)
        wick_bar = None
        for b in bars:
            if b["t"] == target_ms:
                wick_bar = b
                break
            # Also try bars within the 5-min window since screenshot is 5m candle
            if target_ms <= b["t"] < target_ms + 5*60*1000:
                if not wick_bar or b["l"] < (wick_bar.get("l") or 1e9):
                    if direction == "down":
                        wick_bar = b if (not wick_bar or b["l"] < wick_bar["l"]) else wick_bar
                    else:
                        wick_bar = b if (not wick_bar or b["h"] > wick_bar["h"]) else wick_bar

        # Get all 5 1-min bars in the candle window
        candle_bars = [b for b in bars if target_ms <= b["t"] < target_ms + 5*60*1000]
        print(f"\n--- A) Massive 1-min bars inside the 5-min candle window ---")
        if not candle_bars:
            print("  (no 1-min bars in window — Massive sees no trading)")
        else:
            c_lo = min(b["l"] for b in candle_bars)
            c_hi = max(b["h"] for b in candle_bars)
            c_vol = sum(b.get("v", 0) for b in candle_bars)
            print(f"  bars: {len(candle_bars)}   aggregated range: {c_lo} - {c_hi}   total vol: {c_vol:,}")
            for b in candle_bars:
                dt = datetime.fromtimestamp(b["t"]/1000, tz=ET)
                print(f"    {dt.strftime('%H:%M')} ET  O={b['o']:.2f} H={b['h']:.2f} L={b['l']:.2f} C={b['c']:.2f} V={b.get('v',0):,}")
            if direction == "down":
                if c_lo <= extreme + 0.05:
                    print(f"  → Massive 1-min bars DO reach extreme {extreme} (low={c_lo})")
                else:
                    print(f"  → Massive 1-min bars do NOT reach extreme {extreme} (lowest={c_lo}, gap {extreme - c_lo:.2f} below extreme)")
            else:
                if c_hi >= extreme - 0.05:
                    print(f"  → Massive 1-min bars DO reach extreme {extreme} (high={c_hi})")
                else:
                    print(f"  → Massive 1-min bars do NOT reach extreme {extreme} (highest={c_hi})")

        # --- B) 5-min trade tape ---
        s_ns = int(dt_et.timestamp() * 1e9)
        e_ns = int((dt_et + timedelta(minutes=5)).timestamp() * 1e9)
        trades = fetch_trades(ticker, s_ns, e_ns)
        anat = analyze_5min_anatomy(trades, direction, extreme)
        print(f"\n--- B) 5-min trade tape (07:00-07:05 ET / 08:15-08:20 ET) ---")
        if not anat:
            print("  no trades")
        else:
            print(f"  trades: {anat['trades']:,}  vol: {anat['total_size']:,}  ${anat['total_notional_M']:.2f}M  DPR {anat['dpr_pct']:.1f}%")
            print(f"  actual price range: {anat['actual_low']} - {anat['actual_high']}")
            print(f"  prints at/beyond extreme: {anat['prints_at_extreme']}  size {anat['prints_at_extreme_size']:,}")

        # --- C) Forward 10-day fill search via trade tape ---
        print(f"\n--- C) Forward 10-day search for FIRST trade at extreme {extreme} ---")
        end_dt = dt_et + timedelta(days=18)  # buffer for ~10 trading days
        # We'll page day by day to find the first hit cheaply
        first_hit = None
        cursor_day = dt_et.date()
        days_searched = 0
        while days_searched < 14 and not first_hit:
            day_start = datetime.combine(cursor_day, datetime.min.time()).replace(tzinfo=ET)
            day_end = day_start + timedelta(days=1)
            # On the wick day, only look after the candle ends
            if cursor_day == dt_et.date():
                day_start = dt_et + timedelta(minutes=5)
            sn = int(day_start.timestamp() * 1e9)
            en = int(day_end.timestamp() * 1e9)
            day_trades = fetch_trades(ticker, sn, en)
            days_searched += 1
            if direction == "down":
                hits = [t for t in day_trades if (t.get("price") or 1e9) <= extreme + 0.05]
            else:
                hits = [t for t in day_trades if (t.get("price") or 0) >= extreme - 0.05]
            if hits:
                # Sort by participant timestamp ascending
                hits.sort(key=lambda t: int(t.get("participant_timestamp") or t.get("sip_timestamp") or 0))
                first_hit = hits[0]
                first_hit_day_trades = day_trades
                first_hit_day = cursor_day
                break
            # Move to next weekday
            nxt = cursor_day + timedelta(days=1)
            while nxt.weekday() >= 5:
                nxt += timedelta(days=1)
            cursor_day = nxt
            time.sleep(0.1)

        if not first_hit:
            print(f"  No print at/beyond extreme {extreme} within {days_searched} days")
            continue

        ps = first_hit.get("participant_timestamp")
        ss = first_hit.get("sip_timestamp")
        p_et = datetime.fromtimestamp(int(ps)/1e9, tz=ET).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if ps else "?"
        s_et = datetime.fromtimestamp(int(ss)/1e9, tz=ET).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if ss else "?"
        is_dark = first_hit.get("exchange") == 4 and first_hit.get("trf_id") is not None
        delay_s = ((int(ss) - int(ps)) / 1e9) if ps and ss else None
        print(f"  FIRST HIT: px={first_hit.get('price')}  sz={first_hit.get('size')}")
        print(f"             participant_ts: {p_et} ET   sip_ts: {s_et} ET   delay: {delay_s:.2f}s")
        print(f"             exch={first_hit.get('exchange')}  trf={first_hit.get('trf_id')}  dark={is_dark}")
        print(f"             conditions: {first_hit.get('conditions')}")

        # --- D) 5-min around verified hit minute: anatomy ---
        hit_dt = datetime.fromtimestamp(int(ps)/1e9, tz=ET)
        hit_minute_start = hit_dt.replace(second=0, microsecond=0)
        hit_5m_start = hit_minute_start - timedelta(minutes=2)  # ±2 min around hit minute
        hit_5m_end = hit_minute_start + timedelta(minutes=3)
        sn2 = int(hit_5m_start.timestamp() * 1e9)
        en2 = int(hit_5m_end.timestamp() * 1e9)
        hit_window_trades = fetch_trades(ticker, sn2, en2)
        anat2 = analyze_5min_anatomy(hit_window_trades, direction, extreme)
        print(f"\n--- D) 5-min window around verified hit minute ---")
        print(f"  trades: {anat2.get('trades', 0):,}  vol: {anat2.get('total_size',0):,}  ${anat2.get('total_notional_M',0):.2f}M  DPR {anat2.get('dpr_pct',0):.1f}%")
        print(f"  prints at/beyond extreme: {anat2.get('prints_at_extreme',0)}  size {anat2.get('prints_at_extreme_size',0):,}")

        # Of those prints at extreme: how many dark?
        if direction == "down":
            ext_prints = [t for t in hit_window_trades if (t.get("price") or 1e9) <= extreme + 0.05]
        else:
            ext_prints = [t for t in hit_window_trades if (t.get("price") or 0) >= extreme - 0.05]
        ext_dark = [t for t in ext_prints if t.get("exchange") == 4 and t.get("trf_id") is not None]
        ext_sz = sum(t.get("size") or 0 for t in ext_prints)
        ext_dark_sz = sum(t.get("size") or 0 for t in ext_dark)
        print(f"  at-extreme dark%: {(ext_dark_sz/ext_sz*100) if ext_sz else 0:.1f}%   largest at-extreme print: {max((t.get('size') or 0) for t in ext_prints) if ext_prints else 0:,}")
        # Show 3 largest at extreme
        ext_prints_sorted = sorted(ext_prints, key=lambda t: -(t.get("size") or 0))[:3]
        for t in ext_prints_sorted:
            ps2 = t.get("participant_timestamp")
            tstr = datetime.fromtimestamp(int(ps2)/1e9, tz=ET).strftime("%H:%M:%S.%f")[:-3] if ps2 else "?"
            dk = t.get("exchange") == 4 and t.get("trf_id") is not None
            print(f"    {tstr} ET  px={t.get('price')}  sz={t.get('size'):,}  dark={dk}  conds={t.get('conditions')}")

        time.sleep(0.2)


if __name__ == "__main__":
    main()
