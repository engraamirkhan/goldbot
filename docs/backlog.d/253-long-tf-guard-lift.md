---
number: 253
rank: 81
title: 'Lift the 1d/1w settings guard: CONFIRM pass and trading-safety sign-off (BACKLOG 144 blocker 7)'
stage: Engine · GATE
size: XS
status: 'blocked on 251, 252 and a CONFIRM passer · accountable: implementer · reviews: trading-safety-reviewer, quant-reviewer'
---
- **253. Lift the 1d/1w settings guard (BACKLOG 144 blocker 7).** Value: the last step before a qualified 1d/1w arm
  may run on demo. Acceptance: a 1d/1w arm with a CONFIRM pass and a passing lot fit; the settings diff that lists
  1d/1w in `engine.timeframes_live` and empties `GO_LIVE_BLOCKERS_1D_1W` signed off by the trading-safety-reviewer in
  the same change; `tests/test_engine_replay_identity.py` still byte-identical for the existing arms.
