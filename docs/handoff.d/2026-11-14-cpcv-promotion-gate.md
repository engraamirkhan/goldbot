- 2026-11-14 (branch feat/cpcv-gate-1114; ADR 0083 amendment; M16): CPCV/PBO is enforced at promotion. New
  `goldbot/research/cpcv_gate.py`: a gate-passing trial is promoted only with CPCV evidence for the UTC quarter that
  ran on its config and feature version, has no ADR 0003 veto (PBO > 0.5 or most paths negative, verdict recomputed)
  and has PBO whenever its family has other walk-forward trials on its timeframe; missing evidence is run on demand and
  attached to the trial (not a trial: no budget, no DSR count). Wired into the weekly `tournament` (shadow -> live);
  held-back agents and reasons under `cpcv_refused`. Health check `cpcv_evidence` (ops/checks/cpcv.py, tail 180)
  WARNs on a gate-passing trial without usable evidence. Phase gate 1 does not read the evidence yet: escalated as an
  owner decision (ADR 0083 amendment). Needs quant-reviewer and trading-safety-reviewer GOs.
