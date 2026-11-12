- 2026-11-12 (branch feat/retire-loss-arms-2026-11-12; R133; profitability-2026-11-12.md ruling (a)): ZCD, F1A,
  F1C, G1A, G1C, IEX15 and the named controls I0-USTEC, I0-XAUEUR, I0-DE40, I0-US500 (icm-probe) and I0-US30
  (vantage-demo) retire on 2026-11-13 (spec rows `retired_day`/`retired_rule`; id, hash and K unchanged; open
  positions keep every exit). I0-XAUJPY was already retired on its own row (2026-11-09). Why the I0-<instrument> rows
  kept entering after I0 retired: each is its own spec row, stopped only by its instrument's 30-fill cost cell
  (control-thinning point 4), which I0's retirement never covered; the other I0-<key> rows stay under that rule (quant
  ruling). The accounts.yaml registrations are kept so open positions keep their exits (a dropped one is closed as an
  orphan); dropping them and ZCD once flat is backlog 187. ZCD leaves the H-81 pairing (Z9D/Z10D read against
  LCZ9D/LCZ10D); O-12 is recorded abandoned, inconclusive (5 of 100 fills), O-11 likewise (26 of 200). Freed slots go
  to Z10D/Z9D/Z5P through the existing allocation; no cap, RiskGate rule, loss limit or size changed. No engine code
  changed. Tests: tests/test_retire_loss_arms_1112.py; pinned sets updated in tests/test_demo_reset_a2.py,
  tests/test_qualify_before_demo.py, tests/test_execution_controls.py, tests/test_exploration_daily_budget.py.
