- 2026-11-20 stop the bleeding (owner goal "stop known demo losses that carry no information"; system audit
  2026-11-20; docs/research/prereg-amendment-2026-11-20-stop-the-bleeding.md, kill-only). (1) Every gold control on a
  trading account is `control_mode: replay_only`: VEX15, VEX1H, IEX1H, S0R and the red-box RBC15/RBC1H/RBC4H join S0
  (candidates recorded and labelled by the r2 replay, never sent; none retired). VEX15/VEX1H/IEX1H lost GBP 471 on
  99 fills 2026-11-13..20; expected saving GBP 35-70 a week, plus about GBP 140 a week of variance removed. GS1C stays
  broker, paired 1:1 with GS1, until GS1's end on 2026-11-27 (switching it would bias GS1's read: shadow fills carry
  the modelled cost); `tests/test_stop_the_bleeding.py` fails from 2026-11-28 until GS1C is replay_only or retired.
  The disabled weekend lane's WKS0/WKI0 stay broker; the same test fails if vantage-247 is enabled with them so.
  (2) nr4_break 5m (Z6D/Z6V) has been retired since 2026-11-06; a test keeps every nr4_break 5m row retired.
  (3) New engine entry refusal `asia_5m` (`goldbot/engine/entry_refusals.py`): a gold 5m signal decided in
  [00:00, 07:00) UTC is refused and shadowed (window_0207's scope; after it, so [02:00, 07:00) keeps its name).
  No 5m arm enters today, so it binds the next one, hot-loaded discovered arms included. Exits, RiskGate, limits,
  K and every threshold are unchanged. Owner step: none. Deploy: restart the engines (config + engine code).
