---
number: 269
rank: 94
title: 'Model zoo and fusion pre-registration (H-101): implement phase 1 history screen'
stage: 'Research (ML) · implement after quant signature and RD K allocation'
size: M
status: proposed
---
- **269. Model zoo and fusion pre-registration (H-101): implement phase 1 history screen.** Value: answers the owner's 2026-11-25 question (do several models, or fusions of them, beat one model?) for the 10 retired gold arms on the frozen sr-v2 40 inputs, for 8 K; a null retires the family. Card: `docs/research/prereg-2026-11-25-model-zoo-fusion.md`. Acceptance: card section 6 items 1-5 built (`goldbot/research/model_zoo.py`, `goldbot/research/history_replay.py`, `scripts/research/model_zoo_history.py`, `windows.py` registration, `tests/research/test_model_zoo.py`); the four leakage checks of card section 3 pass in tests; Stage 0 count and ADR 0085 power check run outcome-blind through `power_first.start_or_refuse` (NOT STARTED (power) = 0 K); 8 K from 2026Q4 booked at Stage L only after power passes and the registry budget check; gold only until `fix/label-r-quarantine` merges; no stage runs without the quant signature; nothing in goldbot/engine changes.
