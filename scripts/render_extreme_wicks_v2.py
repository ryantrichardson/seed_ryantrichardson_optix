"""
v2: Render charts showing wick day + 10 forward trading days.
The chart must visually answer: did price reach the wick extreme within 10 days?

Layout:
  Top panel: 1-min candles of the wick day (so user can see the ghost spike)
  Bottom panel: daily candles for the next 10 trading days with a horizontal
                line at the wick extreme — touch is visually obvious.
"""
import os, requests, time
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
TICKER = "TSLA"

WICKS = [
    ("2026-03-20", "10:07", "up",   419.25,  "NO"),
    ("2026-02-05", "14:56", "up",   449.08,  "NO"),
    ("2026-04-20", "15:22", "down", 346.14,  "NO"),
    ("2025-12-05", "15:26", "down", 401.25,  "NO"),
    ("2025-12-05", "12:50", "down", 402.11,  "TOUCH d0"),
    ("2026-04-10", "15:08", "up",   385.95,  "TOUCH d0"),
    ("2026-04-22", "11:22", "down", 346.27,  "NO"),
    ("2026-04-10", "15:24", "up",   385.95,  "TOUCH d3"),
    ("2026-02-05", "15:23", "up",   445.01,  "NO"),
    ("2026-04-14", "14:29", "up",   403.32,  "TOUCH d3"),
]


def fetch_day_minute_bars(day_str):
    d = datetime.strptime(day_str, "%Y-%m-%d")
    start = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)
    end   = datetime(d.year, d.month, d.day, 16,  0, tzinfo=ET)
    start_ns = int(start.timestamp() * 1e9)
    end_ns   = int(end.timestamp() * 1e9)
    u = f"{BASE}/v3/trades/{TICKER}"
    p = {"timestamp.gte": start_ns, "timestamp.lt": end_ns, "limit": 50000, "order": "asc"}
    by_minute = defaultdict(list)
    pages = 0
    while u and pages < 200:
        for attempt in range(5):
            try:
                r = S.get(u, params=p if pages == 0 else None, timeout=120); break
            except Exception:
                time.sleep(1 + attempt)
        if r.status_code != 200:
            break
        j = r.json()
        for t in j.get("results", []):
            ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
            if not ts_ns: continue
            ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(ET)
            conds = set(t.get("conditions") or [])
            if conds & {2, 12, 16, 33, 52, 53}: continue
            minute = ts.replace(second=0, microsecond=0)
            by_minute[minute].append(t["price"])
        u = j.get("next_url"); p = None; pages += 1
    bars = []
    for minute in sorted(by_minute):
        prices = sorted(by_minute[minute])
        bars.append({"t": minute, "o": by_minute[minute][0],
                     "h": max(by_minute[minute]), "l": min(by_minute[minute]),
                     "c": by_minute[minute][-1]})
    return bars


def fetch_daily_bars(start_day, n_days_forward=12):
    """Fetch daily OHLC bars from Massive aggregates endpoint."""
    d = datetime.strptime(start_day, "%Y-%m-%d")
    end = d + timedelta(days=n_days_forward + 20)  # buffer for weekends/holidays
    u = f"{BASE}/v2/aggs/ticker/{TICKER}/range/1/day/{start_day}/{end.strftime('%Y-%m-%d')}"
    p = {"adjusted": "true", "sort": "asc", "limit": 50}
    for attempt in range(5):
        try:
            r = S.get(u, params=p, timeout=60); break
        except Exception:
            time.sleep(1 + attempt)
    if r.status_code != 200:
        return []
    j = r.json()
    bars = []
    for x in j.get("results", []):
        ts = datetime.fromtimestamp(x["t"]/1000, tz=timezone.utc).astimezone(ET)
        bars.append({"t": ts, "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"], "v": x.get("v",0)})
    return bars[:n_days_forward + 1]  # wick day + 10 forward


def render(min_bars, daily_bars, ghost_time, ghost_extreme, direction, outcome, day_str, idx, touched_idx):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 8), gridspec_kw={'width_ratios':[1, 1.4]})

    # --- LEFT: intraday wick day ---
    for i, b in enumerate(min_bars):
        color = "#26a69a" if b['c'] >= b['o'] else "#ef5350"
        ax1.plot([i, i], [b['l'], b['h']], color=color, linewidth=0.6, zorder=1)
        bl, bh = min(b['o'],b['c']), max(b['o'],b['c'])
        ax1.add_patch(Rectangle((i-0.35, bl), 0.7, max(bh-bl, 0.01), color=color, zorder=2))
    gh, gm = map(int, ghost_time.split(":"))
    ghost_idx = None
    for i, b in enumerate(min_bars):
        if b['t'].hour == gh and b['t'].minute == gm:
            ghost_idx = i; break
    if ghost_idx is not None:
        ax1.axvline(ghost_idx, color="yellow", alpha=0.5, linewidth=2, zorder=0)
    ax1.axhline(ghost_extreme, color="yellow", linestyle="--", alpha=0.6, linewidth=1.2)
    ax1.text(0.02, 0.97, f"GHOST WICK ${ghost_extreme}", transform=ax1.transAxes,
             color="yellow", fontweight="bold", fontsize=11, verticalalignment="top",
             bbox=dict(boxstyle="round,pad=0.3", facecolor="black", edgecolor="yellow"))
    xt = [i for i,b in enumerate(min_bars) if b['t'].minute % 60 == 0]
    ax1.set_xticks(xt)
    ax1.set_xticklabels([min_bars[i]['t'].strftime("%H:%M") for i in xt], rotation=0, fontsize=9)
    ax1.set_title(f"Wick day {day_str} (1-min)", fontsize=12, fontweight="bold", color="white")
    ax1.set_ylabel("Price", color="white")

    # --- RIGHT: daily candles for wick day + 10 forward ---
    for i, b in enumerate(daily_bars):
        color = "#26a69a" if b['c'] >= b['o'] else "#ef5350"
        ax2.plot([i, i], [b['l'], b['h']], color=color, linewidth=1.5, zorder=1)
        bl, bh = min(b['o'],b['c']), max(b['o'],b['c'])
        ax2.add_patch(Rectangle((i-0.35, bl), 0.7, max(bh-bl, 0.01), color=color, zorder=2))
    # horizontal target line
    ax2.axhline(ghost_extreme, color="yellow", linestyle="--", alpha=0.8, linewidth=1.5)
    # mark wick day (day 0)
    ax2.axvline(0, color="yellow", alpha=0.3, linewidth=2, zorder=0)
    # if touched, draw a green marker on the touch day
    if outcome.startswith("TOUCH"):
        # parse "TOUCH d0" / "TOUCH d3"
        try:
            td = int(outcome.split("d")[1])
            if td < len(daily_bars):
                tb = daily_bars[td]
                touch_price = tb['h'] if direction == "up" else tb['l']
                ax2.plot(td, touch_price, marker="*", markersize=22, color="#00ff00",
                         markeredgecolor="white", markeredgewidth=1.5, zorder=5)
                ax2.annotate(f"TOUCH d{td}", xy=(td, touch_price),
                             xytext=(td, touch_price + (5 if direction=="up" else -5)*(1 if direction=="up" else 1)),
                             color="#00ff00", fontweight="bold", fontsize=11, ha="center",
                             bbox=dict(boxstyle="round,pad=0.2", facecolor="black", edgecolor="#00ff00"))
        except: pass

    ax2.set_xticks(range(len(daily_bars)))
    ax2.set_xticklabels([b['t'].strftime("%m/%d") for b in daily_bars], rotation=45, fontsize=9)
    ax2.set_title(f"Next 10 trading days — target line at ${ghost_extreme}", fontsize=12, fontweight="bold", color="white")
    ax2.text(0.02, 0.97, f"Outcome: {outcome}", transform=ax2.transAxes,
             color="#00ff00" if outcome.startswith("TOUCH") else "#ff6b6b",
             fontweight="bold", fontsize=12, verticalalignment="top",
             bbox=dict(boxstyle="round,pad=0.3", facecolor="black",
                       edgecolor="#00ff00" if outcome.startswith("TOUCH") else "#ff6b6b"))

    # styling
    for ax in (ax1, ax2):
        ax.set_facecolor("#1a1a2e")
        ax.tick_params(colors="white")
        for s in ax.spines.values(): s.set_color("white")
        ax.grid(True, alpha=0.15, color="gray")

    fig.patch.set_facecolor("#0f0f1e")
    fig.suptitle(f"TSLA {day_str} {ghost_time} ET — {direction} wick to ${ghost_extreme}",
                 fontsize=14, fontweight="bold", color="white")
    plt.tight_layout()

    out = f"data/charts/wickv2_{idx:02d}_{day_str.replace('-','')}_{ghost_time.replace(':','')}_{direction}.png"
    os.makedirs("data/charts", exist_ok=True)
    plt.savefig(out, dpi=110, facecolor="#0f0f1e", bbox_inches="tight")
    plt.close()
    print(f"  Saved {out}")
    return out


unique_days = sorted(set(w[0] for w in WICKS))
print(f"Fetching intraday data for {len(unique_days)} wick days")
min_cache = {}
for d in unique_days:
    print(f"  {d}...")
    min_cache[d] = fetch_day_minute_bars(d)

print(f"\nFetching daily bars for forward windows")
daily_cache = {}
for d in unique_days:
    print(f"  {d}...")
    daily_cache[d] = fetch_daily_bars(d, n_days_forward=11)

print("\n=== Rendering ===")
for idx, (day, ttime, direction, extreme, outcome) in enumerate(WICKS, 1):
    print(f"[{idx}] {day} {ttime} {direction} ${extreme} -> {outcome}")
    render(min_cache[day], daily_cache[day], ttime, extreme, direction, outcome, day, idx, None)

print("\nDone.")
