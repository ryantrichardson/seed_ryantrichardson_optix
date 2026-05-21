"""Plot the QQQ exit analysis: MFE/MAE curves + rule comparison + indicator buckets."""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import os

OUT = "data/analysis/qqq_exit"
plt.rcParams["axes.facecolor"] = "#1a1a2e"
plt.rcParams["figure.facecolor"] = "#0f0f1e"
plt.rcParams["axes.edgecolor"] = "white"
plt.rcParams["axes.labelcolor"] = "white"
plt.rcParams["xtick.color"] = "white"
plt.rcParams["ytick.color"] = "white"
plt.rcParams["text.color"] = "white"
plt.rcParams["axes.titlecolor"] = "white"

# --- Plot 1: MFE/MAE per forward day ---
curves = pd.read_csv(f"{OUT}/daily_curves.csv")
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
for ax, entry, title in [(ax1, "E1", "E1: intraday entry (1h after wick)"),
                          (ax2, "E2", "E2: next-day open entry")]:
    sub = curves[curves.entry == entry]
    agg = sub.groupby("day").agg(mfe=("mfe_pct","mean"), mae=("mae_pct","mean")).reset_index()
    ax.plot(agg.day, agg.mfe, "o-", color="#00ff88", linewidth=2, label="MFE (max favorable)")
    ax.plot(agg.day, -agg.mae, "o-", color="#ff5555", linewidth=2, label="-MAE (max adverse)")
    ax.axhline(0, color="white", alpha=0.3, linewidth=1)
    ax.fill_between(agg.day, agg.mfe, -agg.mae, alpha=0.15, color="yellow")
    ax.set_xlabel("Forward day")
    ax.set_ylabel("Move (%)")
    ax.set_title(title, fontweight="bold")
    ax.grid(True, alpha=0.2, color="gray")
    ax.legend(facecolor="#1a1a2e", edgecolor="white", labelcolor="white")
plt.suptitle("QQQ 1-2% Ghost Wick — Forward MFE / MAE", fontsize=14, fontweight="bold")
plt.tight_layout()
plt.savefig(f"{OUT}/plot_mfe_mae.png", dpi=120, bbox_inches="tight")
plt.close()
print("Saved plot_mfe_mae.png")

# --- Plot 2: rule comparison ---
rules = pd.read_csv(f"{OUT}/rules_eval.csv")
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
for ax, entry, title in [(ax1, "E1", "E1 entry"), (ax2, "E2", "E2 entry")]:
    sub = rules[rules.entry == entry].sort_values("avg_ret_pct", ascending=True)
    colors = ["#00ff88" if v > 0 else "#ff5555" for v in sub.avg_ret_pct]
    ax.barh(sub.rule, sub.avg_ret_pct, color=colors)
    for i, (v, wr) in enumerate(zip(sub.avg_ret_pct, sub.win_rate_pct)):
        ax.text(v + (0.05 if v>=0 else -0.05), i, f"{v:+.2f}% ({wr:.0f}% wr)",
                va="center", ha="left" if v>=0 else "right", color="white", fontsize=10)
    ax.axvline(0, color="white", alpha=0.3)
    ax.set_xlabel("Avg return per trade (%)")
    ax.set_title(title, fontweight="bold")
    ax.grid(True, alpha=0.2, color="gray", axis="x")
plt.suptitle("QQQ 1-2% — Exit Rule Comparison", fontsize=14, fontweight="bold")
plt.tight_layout()
plt.savefig(f"{OUT}/plot_rules.png", dpi=120, bbox_inches="tight")
plt.close()
print("Saved plot_rules.png")

# --- Plot 3: indicator bucket plots (E2) ---
df = pd.read_csv(f"{OUT}/per_wick.csv")
e2 = df[df.entry == "E2"].copy()
e2["rsi_5m_bucket"] = pd.cut(e2.rsi_5m_at_wick, bins=[0,30,50,70,100], labels=["<30","30-50","50-70",">70"])
e2["vwap_bucket"] = pd.cut(e2.dist_vwap_pct_at_wick, bins=[-100,-0.5,0,0.5,100], labels=["<-0.5%","-0.5 to 0","0 to 0.5%",">0.5%"])

fig, axes = plt.subplots(1, 3, figsize=(20, 6))

# 3a: hit rate by RSI bucket
a = axes[0]
g = e2.groupby("rsi_5m_bucket", observed=True).agg(n=("touched_target","count"),
                                                    hit=("touched_target","mean"),
                                                    mfe=("peak_mfe_pct","mean")).reset_index()
x = range(len(g))
a.bar(x, g.hit*100, color="#00aaff", width=0.6)
for i, (n, h) in enumerate(zip(g.n, g.hit)):
    a.text(i, h*100 + 2, f"n={n}\n{h*100:.0f}%", ha="center", color="white", fontsize=10, fontweight="bold")
a.set_xticks(x); a.set_xticklabels(g.rsi_5m_bucket)
a.set_ylabel("Hit rate (%)")
a.set_title("Hit rate vs 5-min RSI at wick", fontweight="bold")
a.set_ylim(0, 115)
a.grid(True, alpha=0.2, color="gray", axis="y")

# 3b: hit rate by direction
a = axes[1]
g = e2.groupby("direction", observed=True).agg(n=("touched_target","count"), hit=("touched_target","mean"),
                                               mfe=("peak_mfe_pct","mean"), mae=("peak_mae_pct","mean")).reset_index()
x = range(len(g))
a.bar(x, g.hit*100, color="#00aaff", width=0.6)
for i, (n, h, mfe) in enumerate(zip(g.n, g.hit, g.mfe)):
    a.text(i, h*100 + 2, f"n={n}\n{h*100:.0f}%\nMFE {mfe:+.1f}%", ha="center", color="white", fontsize=10, fontweight="bold")
a.set_xticks(x); a.set_xticklabels(g.direction)
a.set_ylabel("Hit rate (%)")
a.set_title("Hit rate vs wick direction", fontweight="bold")
a.set_ylim(0, 115)
a.grid(True, alpha=0.2, color="gray", axis="y")

# 3c: hit rate by VWAP distance
a = axes[2]
g = e2.groupby("vwap_bucket", observed=True).agg(n=("touched_target","count"), hit=("touched_target","mean")).reset_index()
x = range(len(g))
a.bar(x, g.hit*100, color="#00aaff", width=0.6)
for i, (n, h) in enumerate(zip(g.n, g.hit)):
    a.text(i, h*100 + 2, f"n={n}\n{h*100:.0f}%", ha="center", color="white", fontsize=10, fontweight="bold")
a.set_xticks(x); a.set_xticklabels(g.vwap_bucket)
a.set_ylabel("Hit rate (%)")
a.set_title("Hit rate vs distance from VWAP at wick", fontweight="bold")
a.set_ylim(0, 115)
a.grid(True, alpha=0.2, color="gray", axis="y")

plt.suptitle("QQQ 1-2% — Filter Signals (E2 next-day open entry)", fontsize=14, fontweight="bold")
plt.tight_layout()
plt.savefig(f"{OUT}/plot_indicators.png", dpi=120, bbox_inches="tight")
plt.close()
print("Saved plot_indicators.png")

print("\nDone.")
