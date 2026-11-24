- **Weekly self-retrain (owner 2026-11-24).** This replaces the two retrains retired on 2026-11-13.
  - **Job.** `self_retrain` runs on Sunday at 15:00 UTC (`goldbot/research/self_retrain.py`, aux.db migration 43).
  - **Training data.** Each strategy arm trains on the replay net-R labels of every candidate, taken or not, using its
    point-in-time snapshot.
  - **Gate.** A model becomes ACTIVE only if, out of sample, it beats both no-model and the random control by at
    least +0.05 R, with Holm p <= 0.10 across the arms. Otherwise it stays shadow. A model whose edge decays is
    demoted.
  - **Record.** Every run is written to `self_retrain_runs`.
  - **Health.** `self_retrain` WARNs when the job is off, missed, failed or trained on 0 labels.
  - **K.** One K row is booked per run.
  - **Not served yet.** Nothing in the engine reads it: serving is a separate trading-safety lane.
  - **Card.** `docs/research/prereg-2026-11-24-self-retrain.md` (H-100), awaiting the quant signature.
