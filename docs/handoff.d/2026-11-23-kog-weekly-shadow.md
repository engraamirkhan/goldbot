- 2026-11-23 KoG weekly red-box forward shadow (prereg-2026-11-23-kog-weekly-redbox.md 7.1, quant review changes
  applied to the card). Descriptive only: no order, no arm row, no engine, execution or RiskGate import (a test
  checks the import graph), K +0.
  - `goldbot/research/kog_weekly_shadow.py` scores every KoG "THE KOG REPORT" Sunday post available from
    `tv_ideas.kog_weekly_shadow_from` (2026-11-22) by card section 3 on 1h (primary) and 15m, with control (a)
    (200 seeded block shifts), control (c) (seeded coin) and the P4 twins. A post is scored once its week has closed
    plus 7 days (the twins' week); a hole in the minutes waits, then is final `no_bars`. The frozen extractor file
    `kog_weekly.py` (kogw-1) is unchanged, so its pinned sha still holds.
  - The nightly `tva_scores` job runs it after `score_ideas`; a shadow failure is logged and reported as
    `kog_weekly_shadow.error`, and the ideas' score still completes. The job reports counts only.
  - aux.db migration 42 `kog_weekly_shadow`: one final row per (post, side, tf), or one per post refused whole
    (`one_side_only`, `extractor_abstain`, `no_bars`). Refusal codes `no_signal`, `r_below_3`, `no_targets_left`,
    `entry_after_window`. Control (a) is stored on every side row, fired or not; `risk_usd` is R as a distance.
    No idea text and no price level in any row or log line (a shadow failure logs only the exception type).
  - First rows: the 2026-11-22 post after Friday 2026-12-04's close. Forward precision/recall is counted from the
    2026-11-29 post. A 1h event has at most 15 eligible twins (3 server hours x 5 weekdays), so `twin_n` is stored.
