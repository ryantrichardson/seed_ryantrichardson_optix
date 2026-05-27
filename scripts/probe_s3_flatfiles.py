"""Probe one day of us_stocks_sip/trades_v1 to verify it has the fields we need.

Need to confirm:
- conditions column (cond 7/22/32/53 for GHOST detection)
- trf_id column (dark pool indicator)
- exchange column (FINRA = 4)
- sip_timestamp AND participant_timestamp (need both for lag)
- file size per day
- size after filtering to QQQ + SPY only
"""
import os
import gzip
import io
import time
import boto3
from botocore.config import Config

ACCESS_KEY = os.environ["MASSIVE_S3_ACCESS_KEY_ID"]
SECRET_KEY = os.environ["MASSIVE_S3_SECRET_KEY"]
ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"

s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    config=Config(signature_version="s3v4"),
)

KEY = "us_stocks_sip/trades_v1/2025/12/2025-12-01.csv.gz"

print(f"=== Probing {KEY} ===\n")

# 1) Get file size
head = s3.head_object(Bucket=BUCKET, Key=KEY)
size_mb = head["ContentLength"] / 1024 / 1024
print(f"Compressed file size: {size_mb:.1f} MB ({head['ContentLength']:,} bytes)")
print(f"Last modified: {head['LastModified']}\n")

# 2) Stream-download just the first ~50 MB to see headers and sample rows
print("Downloading first 50 MB to inspect header + sample rows...")
t0 = time.time()
resp = s3.get_object(Bucket=BUCKET, Key=KEY, Range="bytes=0-52428800")
raw = resp["Body"].read()
print(f"  Got {len(raw)/1024/1024:.1f} MB in {time.time()-t0:.1f}s")

# 3) Decompress what we can (partial gzip stream)
print("\nDecompressing partial stream...")
try:
    # Try full decompress first - might fail on partial gzip
    decompressed = gzip.decompress(raw)
except Exception as e:
    # Use streaming decompressor that tolerates truncation
    print(f"  Full decompress failed ({e}), using streaming...")
    dec = gzip.GzipFile(fileobj=io.BytesIO(raw))
    chunks = []
    try:
        while True:
            chunk = dec.read(1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    except (EOFError, OSError) as e:
        print(f"  Streaming stopped at: {e}")
    decompressed = b"".join(chunks)

text = decompressed.decode("utf-8", errors="replace")
print(f"  Decompressed: {len(text)/1024/1024:.1f} MB text")

# 4) Show header + first 5 rows
lines = text.split("\n")
print(f"\n--- Header ---")
print(lines[0])
print(f"\n--- First 5 data rows ---")
for line in lines[1:6]:
    print(line)

# 5) Show column names parsed
header_cols = lines[0].split(",")
print(f"\n--- Columns ({len(header_cols)}) ---")
for i, c in enumerate(header_cols):
    print(f"  [{i}] {c}")

# 6) Check for key fields we need
needed = {
    "conditions": ["conditions", "condition", "cond"],
    "trf_id": ["trf_id", "trf", "trf_timestamp"],
    "exchange": ["exchange", "exch"],
    "sip_timestamp": ["sip_timestamp", "sip"],
    "participant_timestamp": ["participant_timestamp", "participant"],
    "price": ["price"],
    "size": ["size", "volume"],
    "ticker": ["ticker", "symbol"],
}
print(f"\n--- Field check ---")
header_lower = [c.lower().strip() for c in header_cols]
for need, candidates in needed.items():
    found = [c for c in header_lower if any(cand in c for cand in candidates)]
    status = "✓" if found else "✗"
    print(f"  {status} {need}: {found if found else 'NOT FOUND'}")

# 7) Filter to QQQ + SPY rows in this 50MB sample, see ratio
print(f"\n--- QQQ + SPY row sampling ---")
# Find ticker column index
ticker_idx = None
for i, c in enumerate(header_lower):
    if c.strip() in ("ticker", "symbol"):
        ticker_idx = i
        break

if ticker_idx is not None:
    total_rows = 0
    qqq_rows = 0
    spy_rows = 0
    qqq_sample = None
    spy_sample = None
    for line in lines[1:]:
        if not line:
            continue
        total_rows += 1
        cols = line.split(",")
        if len(cols) <= ticker_idx:
            continue
        t = cols[ticker_idx].strip().strip('"')
        if t == "QQQ":
            qqq_rows += 1
            if not qqq_sample:
                qqq_sample = line
        elif t == "SPY":
            spy_rows += 1
            if not spy_sample:
                spy_sample = line
    print(f"  Sample rows scanned: {total_rows:,}")
    print(f"  QQQ rows: {qqq_rows:,}")
    print(f"  SPY rows: {spy_rows:,}")
    if total_rows > 0:
        ratio = (qqq_rows + spy_rows) / total_rows
        print(f"  QQQ+SPY share: {ratio*100:.2f}%")
        print(f"  Projected QQQ+SPY size for full day: {size_mb * ratio:.1f} MB")
    if qqq_sample:
        print(f"\n  Sample QQQ row:\n    {qqq_sample}")
    if spy_sample:
        print(f"\n  Sample SPY row:\n    {spy_sample}")
else:
    print("  Could not find ticker column to filter")

print("\n=== PROBE COMPLETE ===")
