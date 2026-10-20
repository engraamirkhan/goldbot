- Entry mode `account` (ADR 0041): `/mode account` on Telegram, `{"mode": "account"}` on `POST /api/automode` and the
  dashboard's "Use each account's setting" clear the owner's global override, so each engine uses its account's own
  approval mode again (demo auto, real money propose). Owner only, audited (`mode`, `to: account`), no TOTP or
  evidence; `/mode auto` keeps both. The ADR 0036 lock, the re-arm window and the kill switch still force propose.
  Fixes a demo account stuck on propose after `/mode propose`. API contract regenerated.
