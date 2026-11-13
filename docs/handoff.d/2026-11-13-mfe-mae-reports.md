- 2026-11-13 (branch feat/mfe-mae-reports; R147, backlog 195): `perf-report` now reads the R135 excursions of closed
  exploration and control trades in a new "Excursions (MFE / MAE)" section, per family and per arm: median MFE and MAE
  in R with n, the losers' median MFE (R and USD/oz: the move the bracket gave back), the winners' median MAE (how near
  the stop came), the time exits' share, and the stop exits' share of the initial-stop and target exits beside the
  bracket's driftless random-walk null target / (stop + target). Quant review of a97be490: the null has no hold cap, so
  it is printed only when half the trades or more ended at a barrier; a stop exit away from the initial stop (trailed,
  break-even) is counted "moved stop" and left out of the bracket math. Rows closed before
  2026-11-14 carry no fields and are counted "not recorded", never 0 R; `excursion_approx` rows have their own column.
  Descriptive only: no interval, not in the groups note. The attribution exits report already reads MAE/MFE of the
  closed-trade record (backlog 23) and exploration trades never enter that record by design, so it is unchanged; the
  weekly review's exit read (stops after +N R) is unchanged. No API field carries MFE/MAE yet, so the dashboard trade
  detail is backlog 243.
