- 2026-11-27 sr-v3 trainer (PR #488): the quant review's 11 findings are fixed, one commit each. The trainer now runs
  fit-only by default; reads are gated.
  - Fit-only (H2): writes artifacts and `oof_scores.parquet` (no label column; its sha is in the report and every
    manifest) and no S, kept mean, S at 2x cost, D or net-based verdict. `--read` needs every load_labels read
    gate_eligible, the ub95 cost table, and `--trial-id <booked registry trial>` for a judged unit (H3).
  - Read-once (H3, N1): only `--read` books the unit's learn read, at the start of phase 3, in the ledger beside the
    research registry (H4) as family `sr-v3:<unit>`, keyed on the frozen fit-only run (artifact shas, sealed OOF
    scores sha, label spec hash); it needs that fit-only run, refuses a refit that does not reproduce it, and a
    second `--read` raises AlreadyRead. Fit-only runs never book (consumption rows only), so refits after a spec or
    code change never burn the read. G-cw, HB and AVG stay sealed (scores only) until the zoo-v2 card is signed
    (`sr_v3_report.ZOO_V2_CARD`).
  - Costs (H1, M2): `--cost-manifest` (default ~/tmp/sr-v3-trainsets/manifest.json) charges max(W2, ecr ub95) per
    instrument x session x tf at the worse broker, plus the instrument's own swap; a row with no cell is refused.
    Reads use the realised net_read (RT_ub95 + overshoot on stops + nights held x swap); keeps use cost(x). Every
    read states "S at the worse-broker cost" (serving charges the account's own broker). A pooled body (any non-gold
    member) carries cost_version=prior and is never a gate read until FX has a measured cost anchor.
  - Gap (quant ruling 1, clarification of card R-B, K 0): per unit max(1 d, max H over the unit's tfs): 1 d for
    1m-1h, 5 d for 4h, 20 d for 1d+1w; the M1 input-lookback term was withdrawn.
  - Also: collected.json keeps train_end/label_end_max; manifests are recorded before outputs (H5); each G-cw seed
    is its own 90 % resample (M4); N6 shifts whole days circularly by >= 20 trading days and passes per
    (instrument x tf, session) cell (M3); an
    end-to-end truncation test (M5).
  - Modules (LOW): `sr_v3_train` (guarded fit path), `sr_v3_store` (stores and refusals), `sr_v3_report` (read,
    run record, `build_artifact`).
