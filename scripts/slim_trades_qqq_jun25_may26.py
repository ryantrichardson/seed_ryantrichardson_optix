"""Streaming QQQ trade downloader for Jun 2025 - May 2026 (last 360 days).
Same hardened pattern as slim_trades_spy_extend_v2.py:
  - 8 shards
  - Per-day SIGALRM timeout
  - Streaming gzip decode, never fully materializes the daily file in RAM
"""
import os
import gzip
import io
import time
import csv
import signal
from datetime import date, timedelta
import boto3
from botocore.config import Config
from botocore.exceptions import ReadTimeoutError, ConnectTimeoutError

ACCESS_KEY = os.environ["MASSIVE_S3_ACCESS_KEY_ID"]
SECRET_KEY = os.environ["MASSIVE_S3_SECRET_KEY"]
SHARD = int(os.environ.get("SHARD", "1"))
N_SHARDS = int(os.environ.get("N_SHARDS", "8"))

ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"
TICKERS = {"QQQ"}

PER_DAY_TIMEOUT_SEC = 18 * 60  # bumped from 8min — 2025-2026 daily files are ~100M trades vs 60-80M in 2024

s3 = boto3.client(
    "s3", endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY,
    config=Config(signature_version="s3v4",
                  retries={"max_attempts": 4, "mode": "adaptive"},
                  read_timeout=180, connect_timeout=20),
)

START = date(2025, 6, 5)
END = date(2026, 5, 29)

all_days = []
d = START
while d <= END:
    if d.weekday() < 5:
        all_days.append(d)
    d += timedelta(days=1)

my_days = [d for i, d in enumerate(all_days) if i % N_SHARDS == (SHARD - 1)]
print(f"SHARD {SHARD}/{N_SHARDS}: {len(my_days)} days assigned")
print(f"  First: {my_days[0] if my_days else 'none'} -- Last: {my_days[-1] if my_days else 'none'}")
print(f"  Per-day hard timeout: {PER_DAY_TIMEOUT_SEC}s")

OUT_PATH = f"/tmp/slim_trades_qqq_jun25_shard{SHARD}.csv.gz"

stats = {"days_done": 0, "days_skipped": 0, "days_timeout": 0,
         "rows_kept": 0, "rows_scanned": 0}
header_written = False
ticker_idx = None


class DayTimeout(Exception):
    pass


def _alarm_handler(signum, frame):
    raise DayTimeout("per-day timeout")


def process_day(d, writer):
    global header_written, ticker_idx
    key = f"us_stocks_sip/trades_v1/{d.year}/{d.month:02d}/{d.strftime('%Y-%m-%d')}.csv.gz"

    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(PER_DAY_TIMEOUT_SEC)
    try:
        try:
            obj = s3.get_object(Bucket=BUCKET, Key=key)
        except s3.exceptions.NoSuchKey:
            return None, None
        except Exception as e:
            print(f"  {d} get_object error: {e}")
            return None, None

        body = obj["Body"]
        gz = gzip.GzipFile(fileobj=body)
        text_stream = io.TextIOWrapper(gz, encoding="utf-8", errors="replace")
        reader = csv.reader(text_stream)
        try:
            header = next(reader)
        except StopIteration:
            return None, None

        if not header_written:
            writer.writerow(["trade_date"] + header)
            header_written = True
            ticker_idx = header.index("ticker")
            print(f"  Header captured, ticker_idx={ticker_idx}")
        if ticker_idx is None:
            ticker_idx = header.index("ticker")

        kept = 0
        scanned = 0
        date_str = d.strftime("%Y-%m-%d")
        try:
            for row in reader:
                scanned += 1
                if len(row) <= ticker_idx:
                    continue
                if row[ticker_idx] in TICKERS:
                    writer.writerow([date_str] + row)
                    kept += 1
            text_stream.close()
            return kept, scanned
        except DayTimeout:
            print(f"  {d} TIMEOUT after partial read: scanned {scanned:,} kept {kept:,}")
            try: text_stream.close()
            except: pass
            return kept, scanned
        except (ReadTimeoutError, ConnectTimeoutError) as e:
            print(f"  {d} stream-error mid-read: {e}; kept {kept:,}")
            try: text_stream.close()
            except: pass
            return kept, scanned
        except Exception as e:
            print(f"  {d} unexpected mid-read: {e}; kept {kept:,}")
            try: text_stream.close()
            except: pass
            return kept, scanned
    finally:
        signal.alarm(0)


t_start = time.time()
with gzip.open(OUT_PATH, "wt", encoding="utf-8", newline="") as out_f:
    writer = csv.writer(out_f)
    for d in my_days:
        t_day = time.time()
        try:
            kept, scanned = process_day(d, writer)
        except DayTimeout:
            stats["days_timeout"] += 1
            print(f"  {d} HARD-TIMEOUT after {time.time()-t_day:.0f}s — skipped")
            continue
        except Exception as e:
            stats["days_skipped"] += 1
            print(f"  {d} UNCAUGHT ERROR: {e}")
            continue
        if kept is None:
            stats["days_skipped"] += 1
            print(f"  {d} SKIPPED (no data)")
            continue
        stats["rows_kept"] += kept
        stats["rows_scanned"] += scanned
        stats["days_done"] += 1
        elapsed = time.time() - t_day
        print(f"  {d} ok in {elapsed:.0f}s — scanned {scanned:,} kept {kept:,} "
              f"(running total kept: {stats['rows_kept']:,})")

total_elapsed = time.time() - t_start
print(f"\n=== SHARD {SHARD} DONE in {total_elapsed/60:.1f} min ===")
print(f"  Days done:    {stats['days_done']}")
print(f"  Days skipped: {stats['days_skipped']}")
print(f"  Days timeout: {stats['days_timeout']}")
print(f"  Rows kept:    {stats['rows_kept']:,}")
out_size_mb = os.path.getsize(OUT_PATH) / 1024 / 1024
print(f"  Output: {OUT_PATH} ({out_size_mb:.1f} MB)")
