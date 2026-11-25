- **Statistics integrity (plan 2026-11-25 W1-B: S1, S2, S3, S6).**
  - **Holdout guard on every loader (S1, quant R10).** New `goldbot/data/holdout_guard.py`: `refuse` raises
    `HoldoutSealed` on any frame touching 2025-10-01..2026-09-30, `cut`/`split` keep sealed rows out of research
    frames, `pull_spans` keeps every pull out of it. `windows.HoldoutSealed` is the same class.
    - Wired in: the forward replay (`exploration_data.replay_frame_v2` refuses holdout rows and cuts holdout
      bars), r2 labelling, the H-60 prior seed and `ops/m1_backfill` (never asks the broker for a holdout minute;
      a span wholly inside it is refused).
    - Listed: `LIVE_EXEMPT` (live stores, with reasons) and `UNGUARDED` (30 pre-guard scripts, frozen). A test fails
      on any new bar reader outside goldbot/research that is in neither list.
  - **Confirm-window prior leak (S2).** H-60 priors now read the learn window 2010-01..2017-12 only. A 2018-2025
    row needs the arm's confirm read in the read-once ledger (`confirm_read_recorded`). Stored priors with 2019+
    history (the old rule) are no longer read: those arms start flat, and a warning names them.
  - **Overlap design effect (S3, quant Q3).** `power_first.uniqueness` computes Q3's n_eff = sum(u_i) / DEFF_day
    from label entry and exit times. Count-only cards use its expectation via the required `hold_days`. Clone
    pairs count once. Q1's `rho_day_le_60m` (floor 0.05) ships at 0.25 until it is measured. ADR 0085 is amended.
    H-101 is unchanged (overlap 1.0); autodiscovery 4h becomes unreachable.
  - **Discovery stopped (S6).** `research.discovery.stopped: true` overrides a host's `enabled: true`: the night is
    skipped with no run logged, and a non-dry foreground run is refused.
  - `windows.label_embargo` = max(1 d, max hold) is exported for sr-v3.
