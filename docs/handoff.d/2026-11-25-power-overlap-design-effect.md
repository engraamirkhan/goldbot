- **Statistics integrity (plan 2026-11-25 W1-B: S1, S2, S3, S6).**
  - **Holdout guard on every loader (S1, quant R10).** New `goldbot/data/holdout_guard.py`: `refuse` raises
    `HoldoutSealed` on any frame touching 2025-10-01..2026-09-30, `cut`/`split` keep sealed rows out of research
    frames, `pull_spans` keeps every pull out of it. `windows.HoldoutSealed` is the same class.
    - Wired in: the forward replay (`exploration_data.replay_frame_v2` refuses holdout rows and cuts holdout
      bars), r2 labelling, the H-60 prior seed and `ops/m1_backfill` (never asks the broker for a holdout minute;
      a span wholly inside it is refused).
    - `release.read_bar_files`, the default raw/ loader, cuts the holdout unless a gated read passes
      `include_holdout=True` (autodisc's holdout stage, research_pass --score-holdout). raw/ holds the 2025/2026 files.
    - Listed: `LIVE_EXEMPT` (live stores, with reasons) and `UNGUARDED` (20 pre-guard scripts, frozen). A test fails
      on any new bar reader outside goldbot/research that is in neither list.
  - **Confirm-window prior leak (S2).** H-60 priors now read the learn window 2010-01..2017-12 only. A 2018-2025
    row needs the arm's confirm read in the read-once ledger (`confirm_read_recorded`). Stored priors with 2019+
    history (the old rule) are no longer read: those arms start flat, and a warning names them.
  - **Overlap design effect (S3, quant Q3).** `power_first.uniqueness` computes Q3's n_eff = sum(u_i) / DEFF_day
    from label entry and exit times. Count-only cards use its even-arrival expectation (quant C1: Little's law on
    signals, c = signals a day x hold_days, m_eff = m_day / c) via the required `hold_days`. Clone pairs count once.
    Q1's `rho_day_le_60m` (floor 0.05) ships at 0.25 until it is measured. ADR 0085 is amended. H-101's signed
    verdict stands; autodiscovery 1h every-bar and 4h specs become unreachable.
  - **Label spans (N1).** A label decided before the holdout but still open in it is refused in the forward replay
    and dropped from r2 labelling and training (`holdout_guard.refuse_labels` / `cut_labels`).
  - **Static check (C2).** It now also sees live-store reads (`store.read`, `store.sql`, `account_bars_1m`,
    `read_bars`). Research modules that read the live store on purpose are listed with reasons
    (tests/test_holdout_seal.py `LIVE_READ`); live data, ops and engine modules are in `LIVE_EXEMPT`.
  - **Discovery stopped (S6).** `research.discovery.stopped: true` overrides a host's `enabled: true`: the night is
    skipped before any child process with no run logged; the health check ignores it, and a non-dry foreground run is
    refused.
  - `windows.label_embargo` = max(1 d, max hold) is exported for sr-v3.
