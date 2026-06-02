"""NEM trade puller for the last 24 hours (June 1 + June 2 2026).
NO TIME FILTER — keep every trade, including deep overnight (20:00-04:00 ET).
Output: /tmp/slim_trades_nem_24h.csv.gz (trade_date, sip_timestamp, ticker, price, size, conditions, ...)
"""
import os, gzip, io, time, csv, signal
from datetime import date
import boto3
from botocore.config import Config

ACCESS_KEY = os.environ["MASSIVE_S3_ACCESS_KEY_ID"]
SECRET_KEY = os.environ["MASSIVE_S3_SECRET_KEY"]

ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"
TICKERS = {"NEM"}
DAYS = [date(2026, 6, 1), date(2026, 6, 2)]
PER_DAY_TIMEOUT_SEC = 30 * 60

s3 = boto3.client(
    "s3", endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY,
    config=Config(signature_version="s3v4",
                  retries={"max_attempts": 4, "mode": "adaptive"},
                  read_timeout=180, connect_timeout=20),
)

OUT_PATH = "/tmp/slim_trades_nem_24h.csv.gz"

class DayTimeout(Exception): pass
def _alarm_handler(signum, frame): raise DayTimeout()

stats = {"kept": 0, "scanned": 0}
header_written = False
ticker_idx = None

with gzip.open(OUT_PATH, "wt", encoding="utf-8", newline="") as out_f:
    writer = csv.writer(out_f)
    for d in DAYS:
        key = f"us_stocks_sip/trades_v1/{d.year}/{d.month:02d}/{d.strftime('%Y-%m-%d')}.csv.gz"
        print(f"Fetching {key}", flush=True)
        signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(PER_DAY_TIMEOUT_SEC)
        t0 = time.time()
        try:
            try:
                obj = s3.get_object(Bucket=BUCKET, Key=key)
            except s3.exceptions.NoSuchKey:
                print(f"  NO KEY for {d}")
                continue
            body = obj["Body"]
            gz = gzip.GzipFile(fileobj=body)
            text_stream = io.TextIOWrapper(gz, encoding="utf-8", errors="replace")
            reader = csv.reader(text_stream)
            try:
                header = next(reader)
            except StopIteration:
                continue
            if not header_written:
                writer.writerow(["trade_date"] + header)
                header_written = True
                ticker_idx = header.index("ticker")
                print(f"  ticker_idx={ticker_idx}, header={header}")
            kept = 0
            scanned = 0
            date_str = d.strftime("%Y-%m-%d")
            for row in reader:
                scanned += 1
                if len(row) <= ticker_idx:
                    continue
                if row[ticker_idx] in TICKERS:
                    writer.writerow([date_str] + row)
                    kept += 1
            stats["kept"] += kept
            stats["scanned"] += scanned
            print(f"  {d} ok in {time.time()-t0:.0f}s — scanned {scanned:,} kept {kept:,}", flush=True)
        except DayTimeout:
            print(f"  {d} TIMEOUT")
        finally:
            signal.alarm(0)

print(f"\nDONE — kept={stats['kept']:,} scanned={stats['scanned']:,}")
print(f"Output: {OUT_PATH} ({os.path.getsize(OUT_PATH)/1024/1024:.1f} MB)")
