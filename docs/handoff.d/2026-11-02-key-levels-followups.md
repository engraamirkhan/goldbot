- Key-level follow-ups, the three LOW items of the #281 re-review; they must land before the 26 instruments go onto
  icm-probe (ADR 0068).
  - The long-history pull lock is now an `flock` on `state/locks/levels-<box>.lock`, so it holds across engine
    processes. The box is a hash of the bridge host.
  - Every pull resumes at the oldest hole of open market time, so a cut-short head re-pull is filled by the retry.
    Head re-pulls happen at most once a week.
  - Wiring lives in `services.engine.attach_levels`, tested for `long` (a paced client) and `short` (no client, the
    cache still read).
  - The Chart tab says when the strength ranking is still warming up.
