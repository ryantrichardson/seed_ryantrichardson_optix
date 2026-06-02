"""AMZN trades for 2026-06-02 only - quick targeted pull to verify thinkorswim 08:00 ET PBAR."""
import os, gzip, io, time, csv, signal
from datetime import date
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError, ConnectTimeoutError, ResponseStreamingError

ACCESS_KEY = os.environ["MASSIVE_S3_ACCESS_KEY_ID"]
SECRET_KEY = os.environ["MASSIVE_S3_SECRET_KEY"]

s3 = boto3.client("s3", endpoint_url="https://files.massive.com",
    aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY,
    config=Config(signature_version="s3v4",
                  retries={"max_attempts": 5, "mode": "adaptive"},
                  read_timeout=300, connect_timeout=30))

OUT = "/tmp/slim_trades_amzn_jun02.csv.gz"
DAYS = [date(2026, 6, 2)]
TICKERS = {"AMZN"}
PER_DAY_TIMEOUT_SEC = 50*60
MAX_RETRIES = 5

class DayTimeout(Exception): pass
def _h(s,f): raise DayTimeout()

def stream_day(d, writer, write_header):
    key = f"us_stocks_sip/trades_v1/{d.year}/{d.month:02d}/{d.strftime('%Y-%m-%d')}.csv.gz"
    try:
        obj = s3.get_object(Bucket="flatfiles", Key=key)
    except s3.exceptions.NoSuchKey:
        return 0, 0, True, write_header
    except ClientError as e:
        print(f"  ClientError {e.response.get('Error',{}).get('Code')} -- not published yet")
        return 0, 0, True, write_header
    body = obj["Body"]
    gz = gzip.GzipFile(fileobj=body)
    text = io.TextIOWrapper(gz, encoding="utf-8", errors="replace")
    reader = csv.reader(text)
    try:
        header = next(reader)
    except StopIteration:
        return 0, 0, True, write_header
    if write_header:
        writer.writerow(["trade_date"] + header)
        write_header = False
    ticker_idx = header.index("ticker")
    kept = scanned = 0
    ds = d.strftime("%Y-%m-%d")
    try:
        for row in reader:
            scanned += 1
            if len(row) <= ticker_idx: continue
            if row[ticker_idx] in TICKERS:
                writer.writerow([ds]+row); kept += 1
        return kept, scanned, True, write_header
    except (ResponseStreamingError, ReadTimeoutError, ConnectTimeoutError, OSError, EOFError) as e:
        print(f"  stream-broken at scanned={scanned:,} kept={kept:,}: {type(e).__name__}")
        return kept, scanned, False, write_header

t0 = time.time()
with gzip.open(OUT, "wt", encoding="utf-8", newline="") as out_f:
    writer = csv.writer(out_f)
    write_header = True
    for d in DAYS:
        print(f"=== {d} ===")
        best_kept = 0
        best_path = None
        completed = False
        for attempt in range(1, MAX_RETRIES+1):
            tmp = f"/tmp/amzn_{d}_a{attempt}.csv.gz"
            signal.signal(signal.SIGALRM, _h)
            signal.alarm(PER_DAY_TIMEOUT_SEC)
            ta = time.time()
            try:
                with gzip.open(tmp, "wt", encoding="utf-8", newline="") as tf:
                    tw = csv.writer(tf)
                    kept, scanned, completed, _ = stream_day(d, tw, True)
                print(f"  attempt {attempt}: kept={kept:,} scanned={scanned:,} completed={completed} in {time.time()-ta:.0f}s")
                if kept > best_kept:
                    best_kept = kept; best_path = tmp
                if completed: break
                time.sleep(5)
            except DayTimeout:
                print(f"  attempt {attempt}: TIMEOUT after {time.time()-ta:.0f}s")
            finally:
                signal.alarm(0)
        if best_path:
            with gzip.open(best_path, "rt") as tf:
                tr = csv.reader(tf)
                hdr = next(tr)
                if write_header:
                    writer.writerow(hdr); write_header = False
                for row in tr: writer.writerow(row)
            print(f"  locked in {best_kept:,} rows")
print(f"\nDone in {(time.time()-t0)/60:.1f}min  size={os.path.getsize(OUT)/1024/1024:.2f}MB")
