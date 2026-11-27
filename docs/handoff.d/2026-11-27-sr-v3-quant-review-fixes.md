- 2026-11-27 sr-v3 trainer (PR #488): the quant review's 11 findings are fixed, one commit each. The trainer now runs
  fit-only by default; reads are gated.
  - Fit-only (H2): writes artifacts and `oof_scores.parquet` (no label column; its sha is in the report and every
    manifest) and no S, kept mean, S at 2x cost, D or net-based verdict. `--read` needs every load_labels read
    gate_eligible, the ub95 cost table, and `--trial-id <booked registry trial>` for a judged unit (H3).
  - Read-once (H3): each unit's learn read is booked in the ledger beside the research registry (H4) as family
    `sr-v3:<unit>` before its first label read; another run of the unit raises AlreadyRead. G-cw, HB and AVG stay
    sealed (scores only) until the zoo-v2 card is signed (`sr_v3_report.ZOO_V2_CARD`).
  - Costs (H1, M2): `--cost-manifest` (default ~/tmp/sr-v3-trainsets/manifest.json) charges max(W2, ecr ub95) per
    instrument x session x tf at the worse broker, plus the instrument's own swap; a row with no cell is refused.
    Reads use the realised net_read (RT_ub95 + overshoot on stops + nights held x swap); keeps use cost(x).
  - Gap (M1): per unit max(1 d, max H, max input lookback over P30 and served columns): about 129 d for 1m-2h and
    189 d for 4h. For 1d+1w it is 5,670 d, so that unit has no OOF fold on learn 2010-17 until the quant rules
    on it (ADR 0108 decision 4).
  - Also: collected.json keeps train_end/label_end_max; manifests are recorded before outputs (H5); each G-cw seed
    is its own 90 % resample (M4); N6 shuffles within (instrument, session day) and passes per cell (M3); an
    end-to-end truncation test (M5).
  - Modules (LOW): `sr_v3_train` (guarded fit path), `sr_v3_store` (stores and refusals), `sr_v3_report` (read,
    run record, `build_artifact`).
