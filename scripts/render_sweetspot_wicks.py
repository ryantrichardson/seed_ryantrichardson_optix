"""
Render 10 1-2% (sweet spot) ghost wicks with wick day + 10 forward trading days.
Uses PLTR data (44 wicks in sweet spot, 100% hit rate in 6mo backtest).
Layout: left = 1-min wick day, right = daily candles for next 10 trading days.
"""
import os, requests, time
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})
ET = timezone(timedelta(hours=-4))

TICKER = "PLTR"
CSV = f"data/ghost_wicks_v2_{TICKER}_trade.csv"

# Pick 10: 5 up + 5 down, spread across the period
df = pd.read_csv(CSV)
sub = df[(df.wick_pct >= 1) & (df.wick_pct < 2)].copy()
sub = sub.sort_values("date")
ups = sub[sub.direction == "up"].iloc[::max(1, len(sub[sub.direction=="up"])//5)].head(5)
downs = sub[sub.direction == "down"].iloc[::max(1, len(sub[sub.direction=="down"])//5)].head(5)
picked = pd.concat([ups, downs]).sort_values("date").reset_index(drop=True)
print(f"Picked {len(picked)} wicks:")
for _, r in picked.iterrows():
    print(f"  {r.date} {r.time_et} {r.direction} {r.wick_pct:.2f}% extreme=${r.extreme:.2f} touched={r.touched} d={r.days_to_touch}")


def fetch_day_minute_bars(ticker, day_str):
    d = datetime.strptime(day_str, "%Y-%m-%d")
    start = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)
    end   = datetime(d.year, d.month, d.day, 16,  0, tzinfo=ET)
    u = f"{BASE}/v3/trades/{ticker}"
    p = {"timestamp.gte": int(start.timestamp()*1e9), "timestamp.lt": int(end.timestamp()*1e9), "limit": 50000, "order":"asc"}
    by_minute = defaultdict(list)
    pages = 0
    while u and pages < 200:
        for attempt in range(5):
            try: r = S.get(u, params=p if pages == 0 else None, timeout=120); break
            except Exception: time.sleep(1+attempt)
        if r.status_code != 200: break
        j = r.json()
        for t in j.get("results", []):
            ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
            if not ts_ns: continue
            ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(ET)
            conds = set(t.get("conditions") or [])
            if conds & {2,12,16,33,52,53}: continue
            minute = ts.replace(second=0, microsecond=0)
            by_minute[minute].append(t["price"])
        u = j.get("next_url"); p = None; pages += 1
    bars = []
    for minute in sorted(by_minute):
        ps = by_minute[minute]
        bars.append({"t": minute, "o": ps[0], "h": max(ps), "l": min(ps), "c": ps[-1]})
    return bars


def fetch_daily_bars(ticker, start_day, n_forward=11):
    d = datetime.strptime(start_day, "%Y-%m-%d")
    end = d + timedelta(days=n_forward + 20)
    u = f"{BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start_day}/{end.strftime('%Y-%m-%d')}"
    p = {"adjusted":"true","sort":"asc","limit":50}
    for attempt in range(5):
        try: r = S.get(u, params=p, timeout=60); break
        except Exception: time.sleep(1+attempt)
    if r.status_code != 200: return []
    j = r.json()
    bars = []
    for x in j.get("results", []):
        ts = datetime.fromtimestamp(x["t"]/1000, tz=timezone.utc).astimezone(ET)
        bars.append({"t": ts, "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"]})
    return bars[:n_forward + 1]


def render(min_bars, daily_bars, ghost_time, extreme, direction, touched, days_to_touch, wick_pct, day_str, idx, ticker):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 8), gridspec_kw={'width_ratios':[1, 1.4]})

    # LEFT: intraday wick day
    for i, b in enumerate(min_bars):
        color = "#26a69a" if b['c'] >= b['o'] else "#ef5350"
        ax1.plot([i, i], [b['l'], b['h']], color=color, linewidth=0.6, zorder=1)
        bl, bh = min(b['o'],b['c']), max(b['o'],b['c'])
        ax1.add_patch(Rectangle((i-0.35, bl), 0.7, max(bh-bl, 0.005), color=color, zorder=2))
    gh, gm = map(int, ghost_time.split(":"))
    ghost_idx = next((i for i,b in enumerate(min_bars) if b['t'].hour==gh and b['t'].minute==gm), None)
    if ghost_idx is not None:
        ax1.axvline(ghost_idx, color="yellow", alpha=0.5, linewidth=2, zorder=0)
    ax1.axhline(extreme, color="yellow", linestyle="--", alpha=0.7, linewidth=1.2)
    ax1.text(0.02, 0.97, f"GHOST WICK ${extreme:.2f}\n{direction} {wick_pct:.2f}%", transform=ax1.transAxes,
             color="yellow", fontweight="bold", fontsize=10, verticalalignment="top",
             bbox=dict(boxstyle="round,pad=0.3", facecolor="black", edgecolor="yellow"))
    xt = [i for i,b in enumerate(min_bars) if b['t'].minute % 60 == 0]
    ax1.set_xticks(xt); ax1.set_xticklabels([min_bars[i]['t'].strftime("%H:%M") for i in xt], fontsize=9)
    ax1.set_title(f"Wick day {day_str} (1-min)", fontsize=12, fontweight="bold", color="white")
    ax1.set_ylabel("Price", color="white")

    # RIGHT: daily candles for 10 forward days
    for i, b in enumerate(daily_bars):
        color = "#26a69a" if b['c'] >= b['o'] else "#ef5350"
        ax2.plot([i, i], [b['l'], b['h']], color=color, linewidth=1.5, zorder=1)
        bl, bh = min(b['o'],b['c']), max(b['o'],b['c'])
        ax2.add_patch(Rectangle((i-0.35, bl), 0.7, max(bh-bl, 0.01), color=color, zorder=2))
    ax2.axhline(extreme, color="yellow", linestyle="--", alpha=0.8, linewidth=1.5,
                label=f"target ${extreme:.2f}")
    ax2.axvline(0, color="yellow", alpha=0.3, linewidth=2, zorder=0)

    outcome_text = "NO TOUCH (10d)"
    outcome_color = "#ff6b6b"
    if touched and pd.notna(days_to_touch):
        td = int(days_to_touch)
        outcome_text = f"TOUCH d{td}"
        outcome_color = "#00ff00"
        if td < len(daily_bars):
            tb = daily_bars[td]
            tp = tb['h'] if direction=="up" else tb['l']
            ax2.plot(td, tp, marker="*", markersize=24, color="#00ff00",
                     markeredgecolor="white", markeredgewidth=1.5, zorder=5)

    ax2.set_xticks(range(len(daily_bars)))
    ax2.set_xticklabels([b['t'].strftime("%m/%d") for b in daily_bars], rotation=45, fontsize=9)
    ax2.set_title(f"Next 10 trading days — target line at ${extreme:.2f}", fontsize=12, fontweight="bold", color="white")
    ax2.text(0.02, 0.97, outcome_text, transform=ax2.transAxes,
             color=outcome_color, fontweight="bold", fontsize=13, verticalalignment="top",
             bbox=dict(boxstyle="round,pad=0.3", facecolor="black", edgecolor=outcome_color))

    for ax in (ax1, ax2):
        ax.set_facecolor("#1a1a2e"); ax.tick_params(colors="white")
        for s in ax.spines.values(): s.set_color("white")
        ax.grid(True, alpha=0.15, color="gray")
    fig.patch.set_facecolor("#0f0f1e")
    fig.suptitle(f"{ticker} {day_str} {ghost_time} ET — {direction.upper()} wick to ${extreme:.2f} ({wick_pct:.2f}%) — SWEET SPOT 1-2%",
                 fontsize=14, fontweight="bold", color="white")
    plt.tight_layout()
    out = f"data/charts/sweet_{idx:02d}_{ticker}_{day_str.replace('-','')}_{ghost_time.replace(':','')}_{direction}.png"
    os.makedirs("data/charts", exist_ok=True)
    plt.savefig(out, dpi=110, facecolor="#0f0f1e", bbox_inches="tight")
    plt.close()
    print(f"  Saved {out}")
    return out


print("\n=== Fetching + rendering ===")
unique_days = sorted(set(picked.date))
min_cache, daily_cache = {}, {}
for d in unique_days:
    print(f"  Fetching {d}")
    min_cache[d] = fetch_day_minute_bars(TICKER, d)
    daily_cache[d] = fetch_daily_bars(TICKER, d, n_forward=11)

for idx, r in picked.iterrows():
    print(f"\n[{idx+1}] {r.date} {r.time_et} {r.direction} {r.wick_pct:.2f}% -> touched={r.touched} d={r.days_to_touch}")
    render(min_cache[r.date], daily_cache[r.date], r.time_et, r.extreme, r.direction,
           r.touched, r.days_to_touch, r.wick_pct, r.date, idx+1, TICKER)

print("\nDone.")
