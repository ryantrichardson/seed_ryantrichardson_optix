"""Probe Massive's S3 flatfiles bucket.

Goals:
1. Confirm S3 creds work.
2. List top-level prefixes (folder structure).
3. Drill into us_options_opra to see how files are organized.
4. Sample one recent day's file size.
"""
import os
import boto3
from botocore.config import Config

ACCESS_KEY = os.environ["MASSIVE_S3_ACCESS_KEY_ID"]
SECRET_KEY = os.environ["MASSIVE_S3_SECRET_KEY"]

# Massive uses a custom S3-compatible endpoint
ENDPOINT = os.environ.get("MASSIVE_S3_ENDPOINT", "https://files.massive.com")
BUCKET = "flatfiles"

s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    config=Config(signature_version="s3v4"),
)

print(f"=== Probing s3://{BUCKET} via {ENDPOINT} ===\n")

# 1) Top-level prefixes
print("--- Top-level prefixes ---")
try:
    resp = s3.list_objects_v2(Bucket=BUCKET, Delimiter="/", MaxKeys=100)
    prefixes = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
    for p in prefixes:
        print(f"  {p}")
    if not prefixes:
        print("  (none returned - listing top-level keys instead)")
        for obj in resp.get("Contents", [])[:20]:
            print(f"  {obj['Key']}  ({obj['Size']} bytes)")
except Exception as e:
    print(f"ERROR listing top-level: {e}")
    raise

# 2) Drill into us_options_opra
print("\n--- us_options_opra/ structure ---")
try:
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix="us_options_opra/", Delimiter="/", MaxKeys=20)
    for p in resp.get("CommonPrefixes", [])[:20]:
        print(f"  {p['Prefix']}")
except Exception as e:
    print(f"ERROR: {e}")

# 3) Drill into a recent year/month
print("\n--- us_options_opra/2026/ ---")
try:
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix="us_options_opra/2026/", Delimiter="/", MaxKeys=20)
    for p in resp.get("CommonPrefixes", [])[:20]:
        print(f"  {p['Prefix']}")
except Exception as e:
    print(f"ERROR: {e}")

print("\n--- us_options_opra/2025/12/ (sample files) ---")
try:
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix="us_options_opra/2025/12/", MaxKeys=10)
    for obj in resp.get("Contents", [])[:10]:
        size_mb = obj["Size"] / 1024 / 1024
        print(f"  {obj['Key']}  ({size_mb:.1f} MB)")
except Exception as e:
    print(f"ERROR: {e}")

# 4) Sample a recent file size more precisely
print("\n--- Sample sizes for Dec 2025 ---")
try:
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix="us_options_opra/2025/12/", MaxKeys=100)
    sizes = [obj["Size"] for obj in resp.get("Contents", [])]
    if sizes:
        total_gb = sum(sizes) / 1024**3
        avg_mb = (sum(sizes) / len(sizes)) / 1024**2
        print(f"  Files: {len(sizes)}")
        print(f"  Avg per day: {avg_mb:.1f} MB")
        print(f"  Total for month: {total_gb:.2f} GB")
except Exception as e:
    print(f"ERROR: {e}")

print("\n=== PROBE COMPLETE ===")
