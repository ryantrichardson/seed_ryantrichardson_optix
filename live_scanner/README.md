# Live Ghost-Wick Scanner

Real-time ghost-wick detector + dark-pool block tape, streamed over Massive's
WebSocket. Runs locally during market hours.

## What it does

Two things at once, off one WebSocket connection:

1. **Dark-pool block tape** — every off-exchange trade (`exchange == 4` and
   `trf_id` present) with notional ≥ $100K is printed to the console and
   appended to `data/live_blocks.jsonl`. This is the same logic as the
   [Massive blog tutorial](https://massive.com/blog/dark-pool-scanner-with-massive),
   just running alongside the wick detector.

2. **Live ghost-wick detector** — aggregates the trade stream into 1-min bars
   in memory and checks each closed bar against your sweet-spot rule:

   - `body_pct < 0.5%` (quiet candle)
   - `wick/body ratio >= 3`
   - `1.0% <= wick_pct < 2.0%` (the historically high-hit-rate range)
   - Isolated: no neighbor in ±5 minutes has a high/low that reaches 50% of the
     wick depth
   - Mechanical conditions `{2, 12, 16, 33, 52, 53}` excluded from the bar

   When a wick fires, you get a console line, a row in
   `data/live_wick_alerts.jsonl`, and optionally a webhook POST to Slack /
   Discord / Pushover / your own URL.

## Setup

You'll need Python 3.10+ and [uv](https://docs.astral.sh/uv/) installed.

```bash
cd live_scanner
cp .env.example .env
# edit .env and paste your MASSIVE_API_KEY
uv sync
uv run live_scanner.py
```

That's it. Press Ctrl+C to stop.

## Configuration (`.env`)

| Variable             | Default                       | Purpose                                  |
|----------------------|-------------------------------|------------------------------------------|
| `MASSIVE_API_KEY`    | _required_                    | Stocks Advanced plan needed              |
| `TICKERS`            | `TSLA,AMD,NVDA,PLTR,SHOP`     | Comma-separated tickers to watch         |
| `MIN_BLOCK_NOTIONAL` | `100000`                      | Block tape filter ($)                    |
| `WICK_WEBHOOK_URL`   | _empty_                       | Optional: alert webhook                  |

## Sending alerts to your phone

Drop any of these into `WICK_WEBHOOK_URL`:

- **Discord channel webhook** — `https://discord.com/api/webhooks/...`
  (works out of the box; the payload has a `content` field)
- **Slack incoming webhook** — same shape, uses `text`
- **Pushover** — needs a tiny relay, or use a Discord webhook with the Discord
  mobile app (simplest)
- **ntfy.sh** — set `WICK_WEBHOOK_URL=https://ntfy.sh/your-secret-topic` and
  install the ntfy app

The payload is `{"content": "...", "text": "...", "wick": {...}}` so all three
key services pick it up without changes.

## Files written

- `data/live_blocks.jsonl` — one JSON record per qualifying block
- `data/live_wick_alerts.jsonl` — one JSON record per fired wick alert

Both append-only. Safe to `tail -f` them.

## Notes

- **One WebSocket connection per asset class** is the Massive default. If you
  want to run this in two terminals, contact Massive support.
- **EDT vs EST**: timestamps use `-04:00` for display. If we hit DST changes
  this will be off by an hour for display only — wick math is unaffected.
- **Low-volume minutes**: a background thread closes idle 1-min bars every
  15 seconds so a quiet ticker still gets checked.
- **No duplicate alerts**: each minute is checked at most once.
