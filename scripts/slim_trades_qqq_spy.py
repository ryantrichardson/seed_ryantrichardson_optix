"""Stream daily us_stocks_sip/trades_v1 files, filter to QQQ + SPY only, save slim CSV.

Reads SHARD env var (1-4) which determines date range to process.
For each trading day:
  1. Stream-download the gzipped CSV (~2 GB compressed)
  2. Decompress on the fly
  3. Keep only rows where ticker == QQQ or SPY
  4. Append to a per-shard output file

Files are sorted alphabetically by ticker, so QQQ comes well before SPY,
but we just stream the whole thing and filter on each line - simpler and reliable.
"""
import os
import gzip
import io
import time
import csv
from datetime import date, timedelta
import boto3
from botocore.config import Config

ACCESS_KEY = os.environ["MASSIVE_S3_ACCESS_KEY_ID"]
SECRET_KEY = os.environ["MASSIVE_S3_SECRET_KEY"]
SHARD = int(os.environ.get("SHARD", "1"))
N_SHARDS = 4

ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"
TICKERS = {"QQQ", "SPY"}

s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "adaptive"}),
)

# Full date range: 2024-12-01 to 2025-12-31 (matches what we'd want for backtest)
START = date(2024, 12, 1)
END = date(2025, 12, 31)

# Build list of all weekdays (skip Sat/Sun; market holidays will 404 and we skip)
all_days = []
d = START
while d <= END:
    if d.weekday() < 5:  # Mon-Fri
        all_days.append(d)
    d += timedelta(days=1)

# Partition into shards by stride (so each shard has interleaved days, which
# balances any heavy/light day variability better than contiguous chunks)
my_days = [d for i, d in enumerate(all_days) if i % N_SHARDS == (SHARD - 1)]

print(f"SHARD {SHARD}/{N_SHARDS}: assigned {len(my_days)} trading days")
print(f"  First: {my_days[0] if my_days else 'none'}")
print(f"  Last: {my_days[-1] if my_days else 'none'}")

OUT_PATH = f"/tmp/slim_trades_qqq_spy_shard{SHARD}.csv.gz"
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

stats = {"days_done": 0, "days_skipped": 0, "rows_kept": 0, "rows_scanned": 0}
header_written = False

t_start = time.time()
with gzip.open(OUT_PATH, "wt", encoding="utf-8", newline="") as out_f:
    writer = None
    for d in my_days:
        key = f"us_stocks_sip/trades_v1/{d.year}/{d.month:02d}/{d.strftime('%Y-%m-%d')}.csv.gz"
        t_day = time.time()
        try:
            obj = s3.get_object(Bucket=BUCKET, Key=key)
        except s3.exceptions.NoSuchKey:
            print(f"  {d} SKIP (holiday/missing)")
            stats["days_skipped"] += 1
            continue
        except Exception as e:
            print(f"  {d} ERROR: {e}")
            stats["days_skipped"] += 1
            continue

        # Stream-decompress and filter
        body = obj["Body"]
        # Use GzipFile around the raw body stream
        gz = gzip.GzipFile(fileobj=body)
        text_stream = io.TextIOWrapper(gz, encoding="utf-8", errors="replace")
        reader = csv.reader(text_stream)
        try:
            header = next(reader)
        except StopIteration:
            print(f"  {d} EMPTY")
            stats["days_skipped"] += 1
            continue

        if not header_written:
            # Write our header with a trade_date column prepended for clarity
            out_header = ["trade_date"] + header
            writer = csv.writer(out_f)
            writer.writerow(out_header)
            header_written = True
            ticker_idx = header.index("ticker")
            print(f"  Header: ticker_idx={ticker_idx}, cols={header}")

        ticker_idx = header.index("ticker")
        kept = 0
        scanned = 0
        date_str = d.strftime("%Y-%m-%d")
        for row in reader:
            scanned += 1
            if len(row) <= ticker_idx:
                continue
            t = row[ticker_idx]
            if t in TICKERS:
                writer.writerow([date_str] + row)
                kept += 1
        try:
            text_stream.close()
        except Exception:
            pass
        stats["rows_kept"] += kept
        stats["rows_scanned"] += scanned
        stats["days_done"] += 1
        elapsed = time.time() - t_day
        print(f"  {d} ok in {elapsed:.1f}s -- scanned {scanned:,} rows, kept {kept:,} QQQ+SPY  (total kept: {stats['rows_kept']:,})")

total_elapsed = time.time() - t_start
print(f"\n=== SHARD {SHARD} DONE in {total_elapsed/60:.1f} min ===")
print(f"  Days processed: {stats['days_done']}")
print(f"  Days skipped: {stats['days_skipped']}")
print(f"  Total rows scanned: {stats['rows_scanned']:,}")
print(f"  Total rows kept: {stats['rows_kept']:,}")
out_size_mb = os.path.getsize(OUT_PATH) / 1024 / 1024
print(f"  Output: {OUT_PATH} ({out_size_mb:.1f} MB)")
