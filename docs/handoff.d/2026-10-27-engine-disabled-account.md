- **Disabled accounts never trade (SRE finding 2026-10-27).** An engine whose account is `enabled: false` in
  `config/accounts.yaml` refuses to start with exit code 78 (`ENGINE_DISABLED_EXIT`, journalled reason, nothing written
  to `engine_<account>.json` so the supervisor does not halt the enabled accounts); `goldbot-engine@.service`
  `RestartPreventExitStatus=78` and NSSM `AppExit 78 Exit` keep it stopped. With positions of its own open (or an
  unreadable positions read) it starts to wind down: the RiskGate refuses every entry `account_disabled`, exits run as
  always. Live accounts start only enabled and past the phase gate (now checked before any broker connection, same exit
  78; formerly exit 1 and a 10 s restart loop). Tests: `tests/test_engine_disabled_account.py`; RUNBOOK "Engines are
  running but block every entry".
