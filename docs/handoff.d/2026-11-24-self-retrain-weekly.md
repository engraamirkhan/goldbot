- **Weekly self-retrain (owner 2026-11-24).** This replaces the two retrains retired on 2026-11-13.
  - **Job.** `self_retrain` runs on Sunday at 15:00 UTC (`goldbot/research/self_retrain.py`, aux.db migration 43).
  - **Training data.** Each strategy arm trains on the r2 replay net-R labels of every candidate, taken or not, using its
    point-in-time snapshot.
  - **Gate.** A model passes when, out of sample, it beats both no-model and the random control by at least
    +0.05 R with a gross edge >= 0, and the larger of its two p values, Holm-adjusted across the run's judged pairs,
    is at most 0.10. It becomes ACTIVE only after two consecutive passes; otherwise it stays shadow. A model whose
    edge decays is demoted.
  - **Judged arms.** No live strategy arm is left after #424, so the judged arms are 10 pre-named retired gold arms
    (RBR15, Z3D, Z1P, Z9D, Z10D, S1A, S1B, F1A, Z2P, Z5P). Each is read out of sample only on its candidates after
    its retirement. ACTIVE means a re-activation candidate: nothing trades.
  - **Record.** Every run is written to `self_retrain_runs`.
  - **Health.** `self_retrain` WARNs when the job is off, missed, failed or trained on 0 labels.
  - **K.** One K row per judged (broker, arm) pair, at most 10 a run, written before any status change.
  - **Not served yet.** Nothing in the engine reads it: serving is a separate trading-safety lane.
  - **Card.** `docs/research/prereg-2026-11-24-self-retrain.md` (H-100), awaiting the quant signature.
