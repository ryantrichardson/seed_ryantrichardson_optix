# TCT OHLCV Export

`export_tct_ohlcv.py` downloads OHLCV bars from Massive.com across the
Triple Chart Trend (TCT) timeframe set and writes one CSV per timeframe
plus a JSON manifest.

## Timeframes

Daily-class: `13D`, `8D`, `1W`, `3D`, `1D`
Minute-class: `233m`, `144m`, `89m`, `55m`, `34m`, `21m`, `13m`, `8m`, `5m`, `3m`

## Local usage

```bash
export MASSIVE_API_KEY=...   # required for real fetches; never echoed
python scripts/export_tct_ohlcv.py \
  --ticker QQQ \
  --from-date 2026-01-01 \
  --to-date   2026-05-19 \
  --output-dir data/tct_export
```

Convenience commands that need no key:

```bash
python scripts/export_tct_ohlcv.py --help
python scripts/export_tct_ohlcv.py --list-timeframes
python scripts/export_tct_ohlcv.py --ticker QQQ --from-date 2026-01-01 --to-date 2026-05-19 --dry-run
```

## GitHub Actions

Workflow: `.github/workflows/qqq_tct_export.yml` (`workflow_dispatch`).

Inputs:
- `ticker` (default `QQQ`)
- `from_date` (YYYY-MM-DD, required)
- `to_date`   (YYYY-MM-DD, required)

Uses the repository secret `MASSIVE_API_KEY` injected as an env var only.
The key is never echoed or written to logs; HTTP error snippets are
truncated and redacted if the key ever appears in them. CSVs and manifest
are uploaded as the artifact `tct-ohlcv-<ticker>-<from>-<to>`.

## CSV columns

`timestamp_ms, datetime_utc, open, high, low, close, volume, vwap, transactions`
