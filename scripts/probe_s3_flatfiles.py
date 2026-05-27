"""Probe Massive's S3 flatfiles bucket - round 2.

Drill into us_options_opra subfeeds to see exact file structure.
"""
import os
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

def drill(prefix, depth=0, max_depth=5):
    """Recursively list sub-prefixes until we hit actual files."""
    indent = "  " * depth
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix, Delimiter="/", MaxKeys=20)
    subs = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
    files = resp.get("Contents", [])

    if files and not subs:
        # Leaf - show files
        print(f"{indent}{prefix} has {len(files)}+ files (showing 5):")
        for obj in files[:5]:
            size_mb = obj["Size"] / 1024 / 1024
            print(f"{indent}  {obj['Key']}  ({size_mb:.1f} MB)")
        return

    if subs:
        print(f"{indent}{prefix}")
        for s in subs[:10]:
            if depth < max_depth:
                drill(s, depth + 1, max_depth)
            else:
                print(f"{indent}  {s}")

print(f"=== Drilling into us_options_opra subfeeds ===\n")

# Walk minute_aggs (priority for backtest)
print("--- minute_aggs_v1 structure ---")
drill("us_options_opra/minute_aggs_v1/")

print("\n--- quotes_v1 structure ---")
drill("us_options_opra/quotes_v1/")

print("\n--- day_aggs_v1 structure ---")
drill("us_options_opra/day_aggs_v1/")

# Now sample sizes for Dec 2025 minute_aggs
print("\n--- Dec 2025 minute_aggs file sizes ---")
for prefix_try in [
    "us_options_opra/minute_aggs_v1/2025/12/",
    "us_options_opra/minute_aggs_v1/2025-12/",
    "us_options_opra/minute_aggs_v1/2025/",
]:
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix_try, MaxKeys=50)
    contents = resp.get("Contents", [])
    if contents:
        sizes = [obj["Size"] for obj in contents]
        print(f"  Prefix: {prefix_try}")
        print(f"    Found {len(sizes)} files")
        print(f"    Avg: {sum(sizes)/len(sizes)/1024/1024:.1f} MB")
        print(f"    Total: {sum(sizes)/1024**3:.2f} GB")
        print(f"    First 3 keys:")
        for obj in contents[:3]:
            print(f"      {obj['Key']}")
        break

print("\n--- Dec 2025 quotes_v1 file sizes (sample) ---")
for prefix_try in [
    "us_options_opra/quotes_v1/2025/12/",
    "us_options_opra/quotes_v1/2025-12/",
    "us_options_opra/quotes_v1/2025/",
]:
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix_try, MaxKeys=10)
    contents = resp.get("Contents", [])
    if contents:
        sizes = [obj["Size"] for obj in contents]
        print(f"  Prefix: {prefix_try}")
        print(f"    Sample {len(sizes)} files")
        print(f"    Avg: {sum(sizes)/len(sizes)/1024/1024:.1f} MB")
        for obj in contents[:3]:
            size_mb = obj["Size"] / 1024 / 1024
            print(f"      {obj['Key']}  ({size_mb:.1f} MB)")
        break

print("\n=== PROBE 2 COMPLETE ===")
