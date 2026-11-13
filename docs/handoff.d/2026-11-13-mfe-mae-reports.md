- 2026-11-13 (branch feat/mfe-mae-reports; R147, backlog 195): `perf-report` now reads the R135 excursions of closed
  exploration and control trades in a new "Excursions (MFE / MAE)" section, per family and per arm: median MFE and MAE
  in R with n, the losers' median MFE (R and USD/oz: the move the bracket gave back), the winners' median MAE (how near
  the stop came), and the stop exits' share of the stop and target exits beside the bracket's driftless random-walk
  null target / (stop + target) (it ignores the hold cap and the spread, so a gap is a lead). Rows closed before
  2026-11-14 carry no fields and are counted "not recorded", never 0 R; `excursion_approx` rows have their own column.
  Descriptive only: no interval, not in the groups note. The attribution exits report already reads MAE/MFE of the
  closed-trade record (backlog 23) and exploration trades never enter that record by design, so it is unchanged; the
  weekly review's exit read (stops after +N R) is unchanged. No API field carries MFE/MAE yet, so the dashboard trade
  detail is backlog 243.
