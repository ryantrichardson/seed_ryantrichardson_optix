"""
Investigate why ToS shows 13:46 ET candle as O=404.89 H=404.94 L=394.635 C=404.62 V=43.59k
but Massive shows O=403.04 H=403.19 L=402.72 C=402.81 V=48.5k

Hypotheses:
H1: ToS uses end-of-bar timestamps (so ToS 13:46 = Massive 13:45)
H2: ToS uses start-of-bar but plots 1-min bars from a different starting offset
H3: ToS includes condition-flagged prints Massive filters out
H4: There's a separate "consolidated" feed with different prints
H5: The screenshot was actually showing a different time (display bug)

Action: Pull EVERY 1-min bar from 13:30-14:00 ET with full OHLC + volume.
Look for ANY bar whose values match the ToS popup.
Also: For each minute, count prints by condition code to see if filtering matters.
"""
import os, requests, time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = os.environ["MASSIVE_API_KEY"]
BASE = "https://api.massive.com"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {API}"})

# ToS target values
TGT_O = 404.89
TGT_H = 404.94
TGT_L = 394.635
TGT_C = 404.62
TGT_V_K = 43.59  # 43.59k

print("=== ToS popup says: O=404.89 H=404.94 L=394.635 C=404.62 V=43.59k at 13:46 ET ===")
print()

# 1) Get ALL 1-min bars for the day, find ANY bar matching the popup
r = S.get(f"{BASE}/v2/aggs/ticker/TSLA/range/1/minute/2026-05-19/2026-05-19",
          params={"limit": 50000}, timeout=30)
mins = r.json().get("results", [])
print(f"Total 1-min bars from Massive: {len(mins)}")

# Search for ANY bar matching the ToS popup (open AND close within 5 cents)
matches = []
for m in mins:
    if abs(m['o'] - TGT_O) < 0.05 and abs(m['c'] - TGT_C) < 0.05:
        t = datetime.fromtimestamp(m['t']/1000, tz=timezone.utc).astimezone(timezone(timedelta(hours=-4)))
        matches.append((t, m))
        print(f"  MATCH ET {t.strftime('%H:%M')}: O={m['o']} H={m['h']} L={m['l']} C={m['c']} V={m['v']:.0f}")
print(f"Bars with open~404.89 AND close~404.62: {len(matches)}")
print()

# Also search by volume (43.59k = 43590)
print(f"Bars with volume close to 43,590 (\u00b15%):")
for m in mins:
    if 41000 <= m['v'] <= 46000:
        t = datetime.fromtimestamp(m['t']/1000, tz=timezone.utc).astimezone(timezone(timedelta(hours=-4)))
        if t.hour >= 9 and t.hour < 16:
            print(f"  ET {t.strftime('%H:%M')}: O={m['o']:.2f} H={m['h']:.2f} L={m['l']:.2f} C={m['c']:.2f} V={m['v']:.0f}")
print()

# Also search by H,L pattern (high near 404.94 OR low near 394.635)
print(f"Bars with HIGH within 5\u00a2 of 404.94:")
for m in mins:
    if abs(m['h'] - TGT_H) < 0.05:
        t = datetime.fromtimestamp(m['t']/1000, tz=timezone.utc).astimezone(timezone(timedelta(hours=-4)))
        print(f"  ET {t.strftime('%H:%M')}: O={m['o']:.2f} H={m['h']:.2f} L={m['l']:.2f} C={m['c']:.2f} V={m['v']:.0f}")
print()

# H3: Maybe a 5-min aggregate? Or 2-min?
print(f"=== Try 5-min aggregates near 13:46 ET (in case ToS bar is actually 5min displayed wrong) ===")
r = S.get(f"{BASE}/v2/aggs/ticker/TSLA/range/5/minute/2026-05-19/2026-05-19",
          params={"limit": 50000}, timeout=30)
mins5 = r.json().get("results", [])
for m in mins5:
    t = datetime.fromtimestamp(m['t']/1000, tz=timezone.utc).astimezone(timezone(timedelta(hours=-4)))
    if t.hour == 13 and 30 <= t.minute <= 55:
        print(f"  ET {t.strftime('%H:%M')}: O={m['o']:.2f} H={m['h']:.2f} L={m['l']:.2f} C={m['c']:.2f} V={m['v']:.0f}")
print()

# H4: Check if any bars in the day have a range > $5 (very long wick)
print(f"=== All bars where range > $3 (potential wick days) ===")
big = [m for m in mins if (m['h'] - m['l']) > 3]
print(f"Count: {len(big)}")
for m in big[:20]:
    t = datetime.fromtimestamp(m['t']/1000, tz=timezone.utc).astimezone(timezone(timedelta(hours=-4)))
    print(f"  ET {t.strftime('%H:%M')}: O={m['o']:.2f} H={m['h']:.2f} L={m['l']:.2f} C={m['c']:.2f} V={m['v']:.0f}")
