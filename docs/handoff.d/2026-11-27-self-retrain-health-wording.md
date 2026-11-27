- 2026-11-27 health `self_retrain` reasons lead with "next run <slot> UTC" (scheduler.self_retrain).
  - The late-start WARN fires only while the newest run is under a day old: the 11-24 run (slot 11-22,
    `run_if_never_run`) no longer reads "40.9 h late" every day, which the 4 h cycle took for "self_retrain stopped".
  - `model_influence` names the active/shadow models shadow scoring refuses for missing provenance, e.g.
    "0 served: 1 rows refused (no provenance, trained before migration 45)". No provenance backfill.
