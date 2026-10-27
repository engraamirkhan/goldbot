- Arms expansion wave 1 config (arms-expansion-2026-10-26.md sections 3-5, lane L6): 19 new hash-frozen arm ids in
  `config/exploration_arms.yaml` (`rule: specialist`, stop band $3 to the £ cap at 0.01 lot): icm-demo XBB15, XTV1,
  XEQ15, XSO15, XIMNY, XIMLN, XAD1H, XMR15, XMR1H + control C1H; vantage-demo VXRIP1H, VXRT1H, VXFK1H, VXTL1H, VXTV2,
  VXBO1H, VXBO15, VXTR1H + control VC1H. XTV1/VXTV2 (smc) are `blocked` until the ReplicaShadow/spec_ctx path runs on
  the engine's bars (SRE profile 2026-10-26). `config/accounts.yaml`: registered at £17 (15m) / £30 (1h), controls a
  fixed 0.25 share, intraday pot `max_open` 5 -> 9 on both hosts; no other limit changed. The 2026Q4 trial override
  (74 -> 93, `config/settings.yaml`) is NOT in this commit: a PD-owned trial-budget edit, applied by the PD before deploy.
