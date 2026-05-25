#!/usr/bin/env python3
"""
Live ghost-wick detector + dark-pool block tape.

Streams trades from Massive's WebSocket API for a fixed list of tickers, and
does two things simultaneously:

1. DARK-POOL BLOCK TAPE
   - Prints every off-exchange (exchange == 4, trf_id present) trade whose
     notional is >= MIN_BLOCK_NOTIONAL ($100K default).
   - Matches the Massive blog tutorial's logic exactly.

2. LIVE GHOST-WICK DETECTOR
   - Aggregates incoming trades into 1-minute bars per ticker (in memory only).
   - Excludes mechanical condition codes {2, 12, 16, 33, 52, 53} from the bar
     (same exclusion as the historical scanner).
   - When a 1-min bar closes, checks the rule:
       body_pct < 0.5%
       wick/body ratio >= 3
       1.0% <= wick_pct < 2.0%   (sweet spot)
       isolated: no neighbor in +/- 5 min has high/low within 50% of wick depth
   - Fires an alert: console + JSON line in `data/live_wick_alerts.jsonl` +
     optional webhook (Discord/Slack/Pushover).

Run:
    cp .env.example .env  # add MASSIVE_API_KEY
    uv sync
    uv run live_scanner.py
"""
import os
import json
import time
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from massive import WebSocketClient
from massive.websocket.models import EquityTrade, Market

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv()

API_KEY = os.getenv("MASSIVE_API_KEY")
if not API_KEY:
    raise SystemExit("MASSIVE_API_KEY not set. Copy .env.example to .env and fill it in.")

TICKERS = [t.strip().upper() for t in os.getenv("TICKERS", "TSLA,AMD,NVDA,PLTR,SHOP").split(",") if t.strip()]
MIN_BLOCK_NOTIONAL = float(os.getenv("MIN_BLOCK_NOTIONAL", "100000"))
WICK_WEBHOOK_URL = os.getenv("WICK_WEBHOOK_URL", "").strip() or None

# Conditions to exclude when building bars (mechanical / non-price-discovery)
EXCLUDED_CONDS = {2, 12, 16, 33, 52, 53}

# Wick rule thresholds
BODY_PCT_MAX = 0.5          # body must be < 0.5% of price (quiet candle)
WICK_PCT_MIN = 1.0          # sweet spot lower bound
WICK_PCT_MAX = 2.0          # sweet spot upper bound (exclusive)
WICK_BODY_RATIO_MIN = 3.0   # wick must be >= 3x body
NEIGHBOR_WINDOW = 5         # +/- N minutes for isolation check
ISOLATION_BREACH_RATIO = 0.5  # neighbor breaks isolation if it reaches 50% of wick depth

ET = timezone(timedelta(hours=-4))  # crude EDT; good enough for display
TRF_NAMES = {
    201: "FINRA/NYSE TRF",
    202: "FINRA/NASDAQ TRF Carteret",
    203: "FINRA/NASDAQ TRF Chicago",
}

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
ALERT_LOG = DATA_DIR / "live_wick_alerts.jsonl"
BLOCK_LOG = DATA_DIR / "live_blocks.jsonl"

# ---------------------------------------------------------------------------
# Per-ticker bar state
# ---------------------------------------------------------------------------
class TickerState:
    """Holds rolling 1-min bars for one ticker, builds the in-progress bar."""

    def __init__(self, ticker: str):
        self.ticker = ticker
        # Closed bars: deque of dicts {t, o, h, l, c, v}
        self.bars = deque(maxlen=NEIGHBOR_WINDOW * 4 + 2)
        # In-progress bar
        self.curr_minute = None
        self.curr_prices = []
        self.curr_volume = 0
        # Wicks already alerted this minute (avoid double-fire on re-check)
        self.alerted_minutes = set()

    def add_trade(self, price: float, size: int, ts_ms: int, conditions):
        # Skip mechanical / non-price-discovery conditions
        if conditions and (set(conditions) & EXCLUDED_CONDS):
            return None  # no bar closed

        # Bucket by minute in Eastern time
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=ET).replace(second=0, microsecond=0)

        closed_bar = None
        if self.curr_minute is None:
            self.curr_minute = dt
        elif dt > self.curr_minute:
            closed_bar = self._close_current_bar()
            # Fill any missing minutes (no-trade gaps just become empty space; the
            # neighbor window will still work since we keyed by absolute minute).
            self.curr_minute = dt
            self.curr_prices = []
            self.curr_volume = 0
        # else: dt == self.curr_minute, just accumulate

        self.curr_prices.append(price)
        self.curr_volume += size
        return closed_bar

    def _close_current_bar(self):
        if not self.curr_prices:
            return None
        bar = {
            "t": self.curr_minute,
            "o": self.curr_prices[0],
            "h": max(self.curr_prices),
            "l": min(self.curr_prices),
            "c": self.curr_prices[-1],
            "v": self.curr_volume,
        }
        self.bars.append(bar)
        return bar

    def force_close_if_stale(self, now_ts_ms: int):
        """If the in-progress minute is in the past, close it. Returns bar or None."""
        if self.curr_minute is None or not self.curr_prices:
            return None
        now_dt = datetime.fromtimestamp(now_ts_ms / 1000, tz=ET).replace(second=0, microsecond=0)
        if now_dt > self.curr_minute:
            bar = self._close_current_bar()
            self.curr_minute = now_dt
            self.curr_prices = []
            self.curr_volume = 0
            return bar
        return None


STATE = {t: TickerState(t) for t in TICKERS}

# ---------------------------------------------------------------------------
# Wick detection (applied when a bar closes)
# ---------------------------------------------------------------------------
def check_wick(state: TickerState, just_closed):
    """When a bar closes, the bar at index -1-NEIGHBOR_WINDOW is now fully
    surrounded on both sides. Check that candle (and any earlier ones we
    haven't checked yet) for the wick rule."""
    if len(state.bars) < (NEIGHBOR_WINDOW * 2 + 1):
        return []

    # Candle in the middle of the rolling window
    center_idx = len(state.bars) - NEIGHBOR_WINDOW - 1
    b = state.bars[center_idx]

    if b["t"] in state.alerted_minutes:
        return []
    state.alerted_minutes.add(b["t"])

    alerts = []
    body = abs(b["o"] - b["c"])
    upper = b["h"] - max(b["o"], b["c"])
    lower = min(b["o"], b["c"]) - b["l"]
    price = (b["o"] + b["c"]) / 2.0
    if price <= 0:
        return []
    body_pct = body / price * 100.0
    if body_pct >= BODY_PCT_MAX:
        return []

    for direction, wick in (("up", upper), ("down", lower)):
        if wick <= 0:
            continue
        wick_pct = wick / price * 100.0
        if not (WICK_PCT_MIN <= wick_pct < WICK_PCT_MAX):
            continue
        ratio = wick / max(body, 1e-9)
        if ratio < WICK_BODY_RATIO_MIN:
            continue

        body_top = max(b["o"], b["c"])
        body_bot = min(b["o"], b["c"])
        half_depth = wick * ISOLATION_BREACH_RATIO

        # Check neighbors +/- NEIGHBOR_WINDOW. Bars are by absolute minute, so
        # missing minutes (no trades) just won't be in the deque -- that's fine
        # for isolation: absence of trades means no breach.
        isolated = True
        for j_off in range(-NEIGHBOR_WINDOW, NEIGHBOR_WINDOW + 1):
            if j_off == 0:
                continue
            idx = center_idx + j_off
            if idx < 0 or idx >= len(state.bars):
                continue
            nb = state.bars[idx]
            # Must be within +/- N minutes by clock time
            mins_apart = abs((nb["t"] - b["t"]).total_seconds()) / 60.0
            if mins_apart > NEIGHBOR_WINDOW:
                continue
            if direction == "up" and nb["h"] >= body_top + half_depth:
                isolated = False
                break
            if direction == "down" and nb["l"] <= body_bot - half_depth:
                isolated = False
                break
        if not isolated:
            continue

        extreme = b["h"] if direction == "up" else b["l"]
        alerts.append({
            "ticker": state.ticker,
            "time_et": b["t"].strftime("%Y-%m-%d %H:%M"),
            "direction": direction,
            "extreme": round(extreme, 4),
            "open": round(b["o"], 4),
            "close": round(b["c"], 4),
            "body_pct": round(body_pct, 4),
            "wick_pct": round(wick_pct, 4),
            "ratio": round(ratio, 2),
            "volume": b["v"],
        })
    return alerts


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
def log_block(trade: EquityTrade):
    notional = trade.price * trade.size
    venue = TRF_NAMES.get(trade.trf_id, f"TRF {trade.trf_id}")
    ts_et = datetime.fromtimestamp(trade.timestamp / 1000, tz=ET).strftime("%Y-%m-%d %H:%M:%S ET")
    conds = getattr(trade, "conditions", None) or []
    record = {
        "kind": "block",
        "ticker": trade.symbol,
        "price": trade.price,
        "size": trade.size,
        "notional": round(notional, 2),
        "venue": venue,
        "trf_id": trade.trf_id,
        "exchange": trade.exchange,
        "conditions": list(conds),
        "ts_et": ts_et,
    }
    print(
        f"[BLOCK] {ts_et}  {trade.symbol:<6} "
        f"${trade.price:>8.2f} x {trade.size:>6,d}  "
        f"=${notional:>12,.0f}  "
        f"{venue}  cond={list(conds) if conds else '-'}",
        flush=True,
    )
    with BLOCK_LOG.open("a") as f:
        f.write(json.dumps(record) + "\n")


def fire_wick_alert(alert: dict):
    line = (
        f"\n*** WICK ALERT ***  {alert['ticker']} {alert['time_et']} ET  "
        f"{alert['direction'].upper()} {alert['wick_pct']}%  "
        f"extreme=${alert['extreme']}  body={alert['body_pct']}%  ratio={alert['ratio']}  "
        f"vol={alert['volume']:,d}\n"
    )
    print(line, flush=True)
    with ALERT_LOG.open("a") as f:
        f.write(json.dumps({"kind": "wick", **alert}) + "\n")

    if WICK_WEBHOOK_URL:
        try:
            # Generic JSON payload: works for Slack/Discord (content), or your own endpoint
            payload = {
                "content": line.strip(),
                "text": line.strip(),
                "wick": alert,
            }
            requests.post(WICK_WEBHOOK_URL, json=payload, timeout=5)
        except Exception as e:
            print(f"[webhook error] {e}", flush=True)


# ---------------------------------------------------------------------------
# Trade handler
# ---------------------------------------------------------------------------
def handle(msgs):
    for m in msgs:
        if not isinstance(m, EquityTrade):
            continue
        ticker = m.symbol
        state = STATE.get(ticker)
        if state is None:
            continue

        # 1) Block tape: TRF off-exchange print + notional filter
        if m.exchange == 4 and m.trf_id is not None:
            if m.price * m.size >= MIN_BLOCK_NOTIONAL:
                log_block(m)

        # 2) Bar builder + wick check
        closed = state.add_trade(
            price=m.price,
            size=m.size,
            ts_ms=m.timestamp,
            conditions=getattr(m, "conditions", None),
        )
        if closed is not None:
            for alert in check_wick(state, closed):
                fire_wick_alert(alert)


# ---------------------------------------------------------------------------
# Stale-bar sweeper: closes bars during low-volume minutes so the wick check
# still fires when activity is sparse.
# ---------------------------------------------------------------------------
def stale_sweeper():
    while True:
        time.sleep(15)
        now_ms = int(time.time() * 1000)
        for state in STATE.values():
            closed = state.force_close_if_stale(now_ms)
            if closed is not None:
                for alert in check_wick(state, closed):
                    fire_wick_alert(alert)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 72)
    print("LIVE GHOST-WICK + DARK-POOL BLOCK SCANNER")
    print("=" * 72)
    print(f"  Tickers           : {TICKERS}")
    print(f"  Block tape filter : exchange==4 + trf_id present, notional >= ${MIN_BLOCK_NOTIONAL:,.0f}")
    print(f"  Wick rule         : body<{BODY_PCT_MAX}%, wick {WICK_PCT_MIN}-{WICK_PCT_MAX}%, ratio>={WICK_BODY_RATIO_MIN}, isolated +/- {NEIGHBOR_WINDOW} min")
    print(f"  Alerts log        : {ALERT_LOG}")
    print(f"  Blocks log        : {BLOCK_LOG}")
    print(f"  Webhook           : {'configured' if WICK_WEBHOOK_URL else 'OFF (console + disk only)'}")
    print()
    print("Connecting...", flush=True)

    subs = [f"T.{t}" for t in TICKERS]

    # Background thread to close idle minute bars
    threading.Thread(target=stale_sweeper, daemon=True).start()

    client = WebSocketClient(
        api_key=API_KEY,
        market=Market.Stocks,
        subscriptions=subs,
    )
    try:
        client.run(handle_msg=handle)
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        try:
            client.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
