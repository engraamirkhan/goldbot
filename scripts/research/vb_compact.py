"""Compact the per-cell JSON of volatility_bands_diag.py into the breakout-vs-fade table used in the slice F note."""
import json
import sys

cells = json.load(open(sys.argv[1]))
d = {(x["tf"], x["band"], x["type"]): x for x in cells if x["slice"] == "all"}
print("| TF | band | brk n (per month) | brk mean ATR | brk t(day) | brk yrs+ | fade n (per month) | fade mean ATR | fade t(day) | fade yrs+ |")
print("|---|---|---|---|---|---|---|---|---|---|")
for tf in ["15m", "1h", "4h", "1d"]:
    for b in ["KC20x2.0", "BB20x2", "DC20", "DC55", "STARC6x2", "LR50x2", "VWAPx2"]:
        a, f = d.get((tf, b, "brk")), d.get((tf, b, "fade"))
        if not a and not f:
            continue

        def g(x):
            return (f"{x['n']} ({x['per_month']:.0f})", f"{x['mean_atr']:+.3f}", f"{x['t_day']:+.1f}", x["yrs_pos"]) if x else ("-",) * 4

        print("| " + " | ".join([tf, b, *g(a), *g(f)]) + " |")
