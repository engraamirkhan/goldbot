- **Learning liveness judged by output (S5, plan 2026-11-25 W1-C; R180).** On 11-25, 7 learning tables held 0 rows,
  drift_watch and model_watch watched 0 models, and 0 of 22,010 decisions carried a model. Health still said ok.
  - **`learning_liveness`** (goldbot/ops/checks/learning.py): one line for each learner (an aux.db output table and the
    rows that count) and each watcher. A learner that is switched on but has 0 rows, or nothing new within its cadence
    + slack, WARNs. 48 h later it FAILs, and the alert loop sends the FAIL to Telegram. The 48 h clock starts at
    `LIVENESS_FROM` (2026-11-26 22:00 UTC). A learner switched off in settings WARNs with its flag and reason but never
    FAILs. Output that is computed but not applied (Thompson under `allocator: equal`) WARNs. A watcher that watches 0
    models while self_retrain has a shadow or active model WARNs, then FAILs.
  - **`model_influence`**: % of the last 24 h's candidates a model scored and % it changed. It WARNs at 0 % and never
    FAILs, so it never pushes anyone to loosen a gate.
  - **`self_retrain`** also WARNs on: a run more than max_late_hours late, under 50 % of labels used, a broker with 0
    labels, and 0 active models.
  - **`stop_coverage:<account>`**: FAILs when any open position has no broker-side stop.
  - **Noise removed:** up to 3 late broker minutes (sampled bar, then replaced) are an info note on engine:<account>, and
    a missing optional key reads ok with the key named. Target: 0 WARN in steady state.
  - **`scripts/learning_kpis.py`** (read-only): K1 usable rows per broker x class x 9 tf (empty cells shown as 0, history
    rows 0 with the reason), K2 influence with the money effect of changed decisions (day-clustered CI), K4 liveness,
    and PSI of inputs and scores for every shadow or active self-retrain model (`drift.scored_model_drift`).
  - **Open:** drift_watch itself does not yet cover self-retrain models. That needs a goldbot/ops/jobs.py hook
    (another lane's file). The engine reads no self_retrain model, so an ACTIVE filter changes no decision.
