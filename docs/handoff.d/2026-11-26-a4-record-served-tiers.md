- 2026-11-26 ADR 0085 A4 records the D-1 served tiers file `tiers_served_r11-v2.1.csv` (sha256 ff653ea7…, rule blob
  c6f283d7), using the quant's text R-b on #472 verbatim.
  - `dense_labels.RECORDED_TIERS_SHA256` now holds it, which unseals learn-window label reads only.
  - Confirm and test_old reads still need a booked read id (ADR 0104 C-1).
  - w2-point and h_ref = H rows are not gate reads until D-3 and D-4 freeze.
  - The gold learn supply in `config/data/sufficiency.yaml` is now the measured core-12 T2 n_eff:
    5,226 · 3,846 · 2,707 · 1,643 · 1,067 · 0 (2h) · 123 · 0 · 0.
  - No label value was read.
