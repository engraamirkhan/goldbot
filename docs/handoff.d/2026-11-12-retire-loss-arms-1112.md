- 2026-11-12 (branch feat/retire-loss-arms-2026-11-12; R133; profitability-2026-11-12.md ruling (a)): ZCD, F1A,
  F1C, G1A, G1C and IEX15 retire on 2026-11-13 (spec rows `retired_day`/`retired_rule`; id, hash and K unchanged;
  open positions keep every exit). Root cause of the I0 leak: each I0-<instrument> control is its own spec row and
  `spec_retired` read only the row's own `retired_day`, so I0's 2026-11-06 retirement never reached I0-USTEC,
  I0-XAUEUR, I0-DE40, I0-US500 or I0-US30 (they kept entering to 11-12; I0-XAUJPY was already retired on its own row
  on 11-09). The engine now gives a `<family>-<instrument>` row with the family's rule its retired gold family's
  retirement (`exploration.family_retirement`, journalled "family I0 retired: ..."), which stops every registered
  I0-<key> control, the six named ones and the rest still under 30 fills. The six accounts.yaml rows are kept registered
  on purpose: a dropped row's open position is closed at once as an orphan (proposal section 4 offers this engine route
  instead); dropping them and ZCD once flat is backlog 187. Retiring S0 (planned with S1A/S1B) now stops every S0-<key>
  cost control too: backlog 188. Freed slots go to Z10D/Z9D/Z5P through the existing allocation; no cap, RiskGate rule,
  loss limit or size changed. Tests: tests/test_retire_loss_arms_1112.py; pinned sets updated in
  tests/test_demo_reset_a2.py, tests/test_qualify_before_demo.py, tests/test_execution_controls.py,
  tests/test_exploration_daily_budget.py.
