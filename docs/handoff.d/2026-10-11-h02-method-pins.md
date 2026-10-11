---
section: next
---
- H-02 method text made executable (2026-10-11, quant review F1 of the registry writer, before any outcome).
  `docs/research/preregistration-2027Q1.md` H-02 now says:
  - the candidates are tsmom's, variant `{"timeframe": "4h", "max_bars": 12}`: |mean of the 1/5/20-day vol-scaled
    scores| >= 0.5, side = the sign of the score, one at a time;
  - top_k 39 (= max_selected; `side` is the 40th input);
  - permutation is a confirmation filter (`permutation: true`), not a tie-break;
  - freq_threshold and stability_threshold are both 0.6;
  - the full `DiscoveryConfig.model_dump()` is pinned for the registry writer's spec.
  Q2 draft: the H-14 run applies the extra cost frozen in its config (a differing settings value is only reported),
  and H-14's differences from H-02 (stability 0.7, top_k unpinned) are listed for its freeze. Finding for the quant
  reviewer: the 4h continuity row (C4H) has the same config hash as Q4 trial #17 (`tsmom-g0-76d7b19171`); only
  the swap charged differs, and the swap is not in the config.
