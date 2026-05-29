"""Extension: stream SPY trades from May 2024 - Nov 2024 (7 more months).
4 shards (interleaved by stride). Fixes: read_timeout=600, per-day retry on ReadTimeoutError.

Combines with existing slim_data/slim_trades_qqq_spy_shard{1-4}.csv.gz (Dec 2024 - Apr 2025).
Output: /tmp/slim_trades_spy_ext_shard{SHARD}.csv.gz
"""
import os
import gzip
import io
import time
import csv
from datetime import date, timedelta
import boto3
from botocore.config import Config
from botocore.exceptions import ReadTimeoutError, ConnectTimeoutError

ACCESS_KEY = os.environ["MASSIVE_S3_ACCESS_KEY_ID"]
SECRET_KEY = os.environ["MASSIVE_S3_SECRET_KEY"]
SHARD = int(os.environ.get("SHARD", "1"))
N_SHARDS = 4

ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"
TICKERS = {"SPY"}  # SPY only — QQQ extension can come later if needed

s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    config=Config(
        signature_version="s3v4",
        retries={"max_attempts": 8, "mode": "adaptive"},
        read_timeout=600,   # FIX: was default 60s, kept timing out
        connect_timeout=30,
    ),
)

# 7 months going BACK from where existing data starts (2024-12-01)
START = date(2024, 5, 1)
END = date(2024, 11, 30)

all_days = []
d = START
while d <= END:
    if d.weekday() < 5:
        all_days.append(d)
    d += timedelta(days=1)

my_days = [d for i, d in enumerate(all_days) if i % N_SHARDS == (SHARD - 1)]
print(f"SHARD {SHARD}/{N_SHARDS}: assigned {len(my_days)} trading days")
print(f"  First: {my_days[0] if my_days else 'none'} -- Last: {my_days[-1] if my_days else 'none'}")

OUT_PATH = f"/tmp/slim_trades_spy_ext_shard{SHARD}.csv.gz"
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

stats = {"days_done": 0, "days_skipped": 0, "rows_kept": 0, "rows_scanned": 0}
header_written = False
ticker_idx = None

def process_day(d, writer, retries=3):
    """Stream and filter one day. Returns (kept, scanned) or (None, None) if skipped."""
    global header_written, ticker_idx
    key = f"us_stocks_sip/trades_v1/{d.year}/{d.month:02d}/{d.strftime('%Y-%m-%d')}.csv.gz"
    for attempt in range(retries):
        try:
            obj = s3.get_object(Bucket=BUCKET, Key=key)
        except s3.exceptions.NoSuchKey:
            return None, None  # holiday
        except Exception as e:
            print(f"  {d} get_object error (attempt {attempt+1}/{retries}): {e}")
            if attempt + 1 == retries: return None, None
            time.sleep(5 * (attempt + 1))
            continue

        body = obj["Body"]
        gz = gzip.GzipFile(fileobj=body)
        text_stream = io.TextIOWrapper(gz, encoding="utf-8", errors="replace")
        reader = csv.reader(text_stream)
        try:
            header = next(reader)
        except StopIteration:
            return None, None

        if not header_written:
            out_header = ["trade_date"] + header
            writer.writerow(out_header)
            header_written = True
            ticker_idx = header.index("ticker")
            print(f"  Header: ticker_idx={ticker_idx}")

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
        except (ReadTimeoutError, ConnectTimeoutError, Exception) as e:
            print(f"  {d} stream error mid-read (attempt {attempt+1}/{retries}): {e}")
            try: text_stream.close()
            except: pass
            if attempt + 1 == retries:
                return kept, scanned  # partial - keep what we got
            time.sleep(10 * (attempt + 1))

    return None, None


t_start = time.time()
with gzip.open(OUT_PATH, "wt", encoding="utf-8", newline="") as out_f:
    writer = csv.writer(out_f)
    for d in my_days:
        t_day = time.time()
        kept, scanned = process_day(d, writer)
        if kept is None:
            stats["days_skipped"] += 1
            print(f"  {d} SKIPPED")
            continue
        stats["rows_kept"] += kept
        stats["rows_scanned"] += scanned
        stats["days_done"] += 1
        elapsed = time.time() - t_day
        print(f"  {d} ok in {elapsed:.1f}s -- scanned {scanned:,} kept {kept:,}  (total kept: {stats['rows_kept']:,})")

total_elapsed = time.time() - t_start
print(f"\n=== SHARD {SHARD} DONE in {total_elapsed/60:.1f} min ===")
print(f"  Days processed: {stats['days_done']} / skipped {stats['days_skipped']}")
print(f"  Total rows kept: {stats['rows_kept']:,}")
out_size_mb = os.path.getsize(OUT_PATH) / 1024 / 1024
print(f"  Output: {OUT_PATH} ({out_size_mb:.1f} MB)")
