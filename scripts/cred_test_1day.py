"""Single-day credential test for Massive flat-file S3 access.
Tries to pull one recent business day for SPY+QQQ. If creds work, prints row counts.
"""
import os
import gzip
import io
import csv
from datetime import date
import boto3
from botocore.config import Config

ACCESS_KEY = os.environ["MASSIVE_S3_ACCESS_KEY_ID"]
SECRET_KEY = os.environ["MASSIVE_S3_SECRET_KEY"]

ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"
TICKERS = {"SPY", "QQQ"}
TEST_DATE = date(2026, 5, 28)  # Thursday, recent business day

s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    config=Config(
        signature_version="s3v4",
        retries={"max_attempts": 3, "mode": "adaptive"},
        read_timeout=120,
        connect_timeout=20,
    ),
)

key = f"us_stocks_sip/trades_v1/{TEST_DATE.year}/{TEST_DATE.month:02d}/{TEST_DATE.strftime('%Y-%m-%d')}.csv.gz"
print(f"Testing key: {key}")

try:
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    print(f"  HTTP status: {obj['ResponseMetadata']['HTTPStatusCode']}")
    print(f"  Content length: {obj['ContentLength']:,} bytes")
    body = obj["Body"].read()
    print(f"  Bytes read: {len(body):,}")
    decompressed = gzip.decompress(body)
    print(f"  Decompressed: {len(decompressed):,} bytes")
    reader = csv.reader(io.StringIO(decompressed.decode()))
    header = next(reader)
    print(f"  Header columns: {header}")
    ticker_idx = header.index("ticker")
    counts = {t: 0 for t in TICKERS}
    total = 0
    for row in reader:
        total += 1
        if row[ticker_idx] in TICKERS:
            counts[row[ticker_idx]] += 1
    print(f"  Total rows in file: {total:,}")
    for t, c in counts.items():
        print(f"  {t} rows: {c:,}")
    print("CREDENTIALS WORK — flat-file access confirmed")
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
    raise
