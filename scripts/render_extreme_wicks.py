"""
Render charts for the 5%+ TSLA ghost wicks from the 6-month backtest.
Top 10 most extreme wicks from the run:
  2026-03-20 10:07 up wick 12.87% extreme=$419.25 -> NO
  2026-02-05 14:56 up wick 12.75% extreme=$449.08 -> NO
  2026-04-20 15:22 down wick 11.69% extreme=$346.14 -> NO
  2025-12-05 15:26 down wick 11.68% extreme=$401.25 -> NO
  2025-12-05 12:50 down wick 11.52% extreme=$402.11 -> TOUCH d0
  2026-04-10 15:08 up wick 11.46% extreme=$385.95 -> TOUCH d0
  2026-04-22 11:22 down wick 11.33% extreme=$346.27 -> NO
  2026-04-10 15:24 up wick 11.30% extreme=$385.95 -> TOUCH d3
  2026-02-05 15:23 up wick 11.09% extreme=$445.01 -> NO
  2026-04-14 14:29 up wick 10.92% extreme=$403.32 -> TOUCH d3

For each, fetch ALL trades for that day, build 1-min bars (including TRF
prints, excluding only true mechanical conditions), and render a candlestick
chart with a marker on the ghost candle. Save to PNG.
"""
import os, requests, time, csv, json
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
    """Fetch all trades for the day, build 1-min bars locally."""
    d = datetime.strptime(day_str, "%Y-%m-%d")
    # Use UTC-4 (EDT) — the user's region uses DST during this window
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
            print(f"  HTTP {r.status_code}")
            break
        j = r.json()
        for t in j.get("results", []):
            ts_ns = t.get("participant_timestamp") or t.get("sip_timestamp")
            if not ts_ns:
                continue
            ts = datetime.fromtimestamp(ts_ns/1e9, tz=timezone.utc).astimezone(ET)
            conds = set(t.get("conditions") or [])
            if conds & {2, 12, 16, 33, 52, 53}:
                continue
            minute = ts.replace(second=0, microsecond=0)
            by_minute[minute].append({"price": t["price"], "size": t.get("size", 0), "ts": ts})
        u = j.get("next_url"); p = None; pages += 1

    bars = []
    for minute in sorted(by_minute):
        trades = sorted(by_minute[minute], key=lambda x: x["ts"])
        prices = [tr["price"] for tr in trades]
        bars.append({
            "t": minute,
            "o": prices[0], "h": max(prices), "l": min(prices), "c": prices[-1],
            "v": sum(tr["size"] for tr in trades)
        })
    return bars

def render_chart(bars, ghost_time, ghost_extreme, direction, outcome, day_str, idx):
    """Render candlestick chart with the ghost wick highlighted."""
    if not bars:
        print(f"  No bars to plot")
        return

    fig, (ax, axv) = plt.subplots(2, 1, figsize=(14, 8),
                                   gridspec_kw={'height_ratios':[4,1]}, sharex=True)

    # Plot candles
    for i, b in enumerate(bars):
        color = "#26a69a" if b['c'] >= b['o'] else "#ef5350"  # green/red
        # Wick
        ax.plot([i, i], [b['l'], b['h']], color=color, linewidth=0.8, zorder=1)
        # Body
        body_low = min(b['o'], b['c'])
        body_high = max(b['o'], b['c'])
        height = max(body_high - body_low, 0.01)
        ax.add_patch(Rectangle((i-0.35, body_low), 0.7, height, color=color, zorder=2))
        # Volume
        axv.bar(i, b['v'], width=0.7, color=color, alpha=0.6)

    # Highlight ghost wick
    ghost_hh, ghost_mm = map(int, ghost_time.split(":"))
    ghost_idx = None
    for i, b in enumerate(bars):
        if b['t'].hour == ghost_hh and b['t'].minute == ghost_mm:
            ghost_idx = i
            break
    if ghost_idx is not None:
        gb = bars[ghost_idx]
        # Vertical highlight line
        ax.axvline(ghost_idx, color="yellow", alpha=0.4, linewidth=2, zorder=0)
        # Arrow to extreme
        ax.annotate(f"GHOST WICK\n{direction} ${ghost_extreme}\nBody O={gb['o']:.2f} C={gb['c']:.2f}",
                    xy=(ghost_idx, ghost_extreme),
                    xytext=(ghost_idx + 15, ghost_extreme + (5 if direction=="down" else -5)),
                    fontsize=11, color="yellow", fontweight="bold",
                    arrowprops=dict(arrowstyle="->", color="yellow", lw=2),
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="black", edgecolor="yellow"))
        # Horizontal line at extreme level
        ax.axhline(ghost_extreme, color="yellow", linestyle="--", alpha=0.5, linewidth=1)

    # X-axis labels: show every 30 min
    xticks = [i for i, b in enumerate(bars) if b['t'].minute % 30 == 0]
    xlabels = [bars[i]['t'].strftime("%H:%M") for i in xticks]
    axv.set_xticks(xticks)
    axv.set_xticklabels(xlabels, rotation=45, fontsize=9)

    ax.set_title(f"TSLA {day_str} — Ghost wick at {ghost_time} ET, {direction} to ${ghost_extreme}\n"
                 f"Outcome: {outcome}",
                 fontsize=13, fontweight="bold", color="white")
    ax.set_ylabel("Price", color="white")
    axv.set_ylabel("Volume", color="white")
    ax.set_facecolor("#1a1a2e")
    axv.set_facecolor("#1a1a2e")
    fig.patch.set_facecolor("#0f0f1e")
    ax.tick_params(colors="white")
    axv.tick_params(colors="white")
    for s in ax.spines.values(): s.set_color("white")
    for s in axv.spines.values(): s.set_color("white")
    ax.grid(True, alpha=0.15, color="gray")
    plt.tight_layout()

    safe_day = day_str.replace("-", "")
    safe_time = ghost_time.replace(":", "")
    out = f"data/charts/wick_{idx:02d}_{safe_day}_{safe_time}_{direction}.png"
    os.makedirs("data/charts", exist_ok=True)
    plt.savefig(out, dpi=110, facecolor="#0f0f1e", bbox_inches="tight")
    plt.close()
    print(f"  Saved {out}")
    return out

# Process unique dates only
days_to_fetch = sorted(set(w[0] for w in WICKS))
print(f"Fetching trade data for {len(days_to_fetch)} unique days: {days_to_fetch}")
bars_cache = {}
for day in days_to_fetch:
    print(f"\n=== Fetching {day} ===")
    bars_cache[day] = fetch_day_minute_bars(day)
    print(f"  {len(bars_cache[day])} bars built")

print("\n=== Rendering charts ===")
for idx, (day, time_et, direction, extreme, outcome) in enumerate(WICKS, 1):
    print(f"\n[{idx}] {day} {time_et} {direction} ${extreme} -> {outcome}")
    bars = bars_cache.get(day, [])
    render_chart(bars, time_et, extreme, direction, outcome, day, idx)

print("\nDone.")
