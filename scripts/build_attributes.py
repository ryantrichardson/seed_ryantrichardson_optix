"""Generate complete attributes.INI for TradeStation 3rd-party data import."""
from pathlib import Path

TICKERS = ["AMD", "OWL", "PYPL", "SHOP", "SLV", "SOXL"]

SYMBOLS = [
    ("SVR_TS",          "Short Volume Ratio (all venues)"),
    ("SVR_NASDAQ_TS",   "NASDAQ TRF Short Volume %"),
    ("DPR_TS",          "Dark Pool Ratio (% notional)"),
    ("DPR_BLOCKS_TS",   "Dark Pool Ratio (blocks only)"),
    ("DPR_CARTERET_TS", "Carteret TRF (% notional)"),
]

HEADER = ("SYMBOL,CATEGORY,DATE FORMAT,EXCHANGE,PRICE SCALE,MINIMUM MOVEMENT,"
          "BIG POINT VALUE,SESSION 1 START TIME,SESSION 1 END TIME,SESSION 1 DAYS,"
          "DESCRIPTION,SESSION 2 START TIME,SESSION 2 END TIME,SESSION 2 DAYS,"
          "OPTION TYPE,STRIKE PRICE,DAILY LIMIT,MARGIN,EXPIRATION DATE,LOCALE")

lines = [HEADER]
for t in TICKERS:
    for suffix, desc_tail in SYMBOLS:
        symbol = f"{t}_{suffix}"
        description = f"{t} {desc_tail}"
        row = (f'{symbol},STOCK,MM/DD/YYYY,NASDAQ,1/100,1,1.00,0930,1600,MTWRF,'
               f'"{description}",,,,,,,,,0x409')
        lines.append(row)

out = Path(__file__).resolve().parent.parent / "data" / "attributes.INI"
out.write_text("\n".join(lines) + "\n")
print(f"Wrote {out} with {len(lines)-1} symbol rows")
