"""NEM trade puller for the last 24 hours (June 1 + June 2 2026).
NO TIME FILTER — keep every trade, including deep overnight (20:00-04:00 ET).
Retries each day up to 5x on stream break.
Output: /tmp/slim_trades_nem_24h.csv.gz
"""
import os, gzip, io, time, csv, signal
from datetime import date
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError, ConnectTimeoutError, ResponseStreamingError

ACCESS_KEY = os.environ["MASSIVE_S3_ACCESS_KEY_ID"]
SECRET_KEY = os.environ["MASSIVE_S3_SECRET_KEY"]

ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"
TICKERS = {"NEM"}
DAYS = [date(2026, 6, 1), date(2026, 6, 2)]
PER_DAY_TIMEOUT_SEC = 50 * 60
MAX_DAY_RETRIES = 5

s3 = boto3.client(
    "s3", endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY,
    config=Config(signature_version="s3v4",
                  retries={"max_attempts": 5, "mode": "adaptive"},
                  read_timeout=300, connect_timeout=30),
)

OUT_PATH = "/tmp/slim_trades_nem_24h.csv.gz"

class DayTimeout(Exception): pass
def _alarm_handler(signum, frame): raise DayTimeout()

stats = {"kept_per_day": {}, "scanned_per_day": {}}
header_written = False
ticker_idx = None

def stream_day(d, writer):
    """One attempt at streaming a full day. Returns (kept, scanned, completed_bool)."""
    global header_written, ticker_idx
    key = f"us_stocks_sip/trades_v1/{d.year}/{d.month:02d}/{d.strftime('%Y-%m-%d')}.csv.gz"
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
    except s3.exceptions.NoSuchKey:
        print(f"  NO KEY for {d}")
        return 0, 0, True
    except ClientError as e:
        code = e.response.get('Error', {}).get('Code')
        print(f"  {d} ClientError {code} — skipping (likely not yet published)")
        return 0, 0, True

    body = obj["Body"]
    gz = gzip.GzipFile(fileobj=body)
    text_stream = io.TextIOWrapper(gz, encoding="utf-8", errors="replace")
    reader = csv.reader(text_stream)
    try:
        header = next(reader)
    except StopIteration:
        return 0, 0, True

    if not header_written:
        writer.writerow(["trade_date"] + header)
        header_written = True
        ticker_idx = header.index("ticker")
        print(f"  header captured, ticker_idx={ticker_idx}", flush=True)
    if ticker_idx is None:
        ticker_idx = header.index("ticker")

    kept, scanned = 0, 0
    date_str = d.strftime("%Y-%m-%d")
    try:
        for row in reader:
            scanned += 1
            if len(row) <= ticker_idx:
                continue
            if row[ticker_idx] in TICKERS:
                writer.writerow([date_str] + row)
                kept += 1
        return kept, scanned, True
    except (ResponseStreamingError, ReadTimeoutError, ConnectTimeoutError, OSError, EOFError) as e:
        print(f"  {d} stream-broken at scanned={scanned:,} kept={kept:,} — {type(e).__name__}: {str(e)[:200]}", flush=True)
        return kept, scanned, False
    except Exception as e:
        print(f"  {d} unexpected at scanned={scanned:,} kept={kept:,} — {type(e).__name__}: {str(e)[:200]}", flush=True)
        return kept, scanned, False

t_start = time.time()
with gzip.open(OUT_PATH, "wt", encoding="utf-8", newline="") as out_f:
    writer = csv.writer(out_f)
    for d in DAYS:
        print(f"\n=== Day {d} ===", flush=True)
        best_kept = 0
        best_scanned = 0
        attempt = 0
        completed = False
        # Each attempt: try once, get whatever it gives us. We want the BEST (most complete) attempt.
        # Strategy: if first attempt gets > 0 rows but doesn't complete, retry hoping for full read.
        # Use a SEPARATE temp file per attempt then keep the best.
        while attempt < MAX_DAY_RETRIES and not completed:
            attempt += 1
            tmp_path = f"/tmp/slim_trades_nem_{d}_attempt{attempt}.csv.gz"
            signal.signal(signal.SIGALRM, _alarm_handler)
            signal.alarm(PER_DAY_TIMEOUT_SEC)
            t0 = time.time()
            try:
                with gzip.open(tmp_path, "wt", encoding="utf-8", newline="") as tmp_f:
                    tmp_writer = csv.writer(tmp_f)
                    # We want header in tmp file too if we end up using it
                    # but main file already has header (if header_written). For simplicity always write header to tmp.
                    kept, scanned, completed = stream_day(d, tmp_writer)
                print(f"  attempt {attempt}: kept={kept:,} scanned={scanned:,} completed={completed} in {time.time()-t0:.0f}s", flush=True)
                if kept > best_kept:
                    best_kept = kept
                    best_scanned = scanned
                    best_tmp = tmp_path
                if not completed:
                    print(f"  retrying day {d}...", flush=True)
                    time.sleep(5)
            except DayTimeout:
                print(f"  attempt {attempt}: TIMEOUT after {time.time()-t0:.0f}s", flush=True)
                # could not check if partial file has rows — assume failure
            except Exception as e:
                print(f"  attempt {attempt}: outer error: {e}", flush=True)
            finally:
                signal.alarm(0)
        # Now append the best attempt's data to the main output
        if best_kept > 0:
            with gzip.open(best_tmp, "rt", encoding="utf-8") as tmp_f:
                tmp_reader = csv.reader(tmp_f)
                next(tmp_reader)  # skip header in tmp file
                for row in tmp_reader:
                    writer.writerow(row)
            stats["kept_per_day"][str(d)] = best_kept
            stats["scanned_per_day"][str(d)] = best_scanned
            print(f"  ✓ {d}: locked in best attempt with {best_kept:,} rows (completed={completed})", flush=True)
        else:
            stats["kept_per_day"][str(d)] = 0
            print(f"  ✗ {d}: no data captured after {attempt} attempts", flush=True)

print(f"\n=== DONE in {(time.time()-t_start)/60:.1f}min ===")
for k, v in stats["kept_per_day"].items():
    print(f"  {k}: {v:,} NEM rows")
print(f"Output: {OUT_PATH} ({os.path.getsize(OUT_PATH)/1024/1024:.2f} MB)")
