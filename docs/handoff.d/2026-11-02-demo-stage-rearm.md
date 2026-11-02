- ADR 0049 D3 / T1 demo roll (`goldbot/engine/demo_roll.py`): a demo `probe_only` host paused at -8% no longer waits for the owner. The roll runs at the next 00:00 UTC risk-day roll, and only if all of these hold:
  - the effective approval mode (owner /mode, else the account's, through the auto lock) is auto, and the host is outside the propose-only window that follows an owner re-arm;
  - no owner, supervisor or drift halt stands;
  - the terminal reports demo on a fresh read;
  - the host is at least 8% and under 12% below its mark, with no 12% read pending;
  - it is flat except the scheduled cost probe (P0).

  The roll resets the mark to max(balance, equity), sets the stage to NORMAL and advances the epoch. The day and week starts, dynamic limits, positions, stops and exits are untouched. Each roll is journalled `rotation`. Once the risk state is saved, the owner gets a Telegram notice as information. A refusal is journalled `rotation_refused` with its reason and is tried again at the next roll. The epoch is persisted in `risk_<account>.json` (`epoch`, `epoch_utc`), written only once a host has rolled. Live, paper and non-probe accounts return before any read. A source-hash pin in `tests/test_demo_roll.py` holds the owner's TOTP re-arm path, the stage machine and the dynamic-limit reset unchanged. Trading-safety accepted T1 on PR #290 with findings F1-F6 applied, and the ADR records the acceptance. Not built yet: T2 (top-up guard), T4 (tripwires), T5 (ledger epoch rows by sleeve) and T6 (the dashboard's two drawdowns).
