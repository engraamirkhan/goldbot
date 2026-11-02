---
number: 143
rank: 51
title: 'goldbot accounts delete <key>: remove a secret from the store'
stage: 'Ops (secrets)'
size: S
status: 'proposed, severity LOW · accountable: implementer · reviews: code-reviewer, security-reviewer'
---
- **143. goldbot accounts delete <key>: remove a secret from the store.** Value: retiring a feature leaves its secrets behind (ADR 0066: `tradingview-sessionid`, `-sign`, `tradingview-webhook-secret`, `tradingview-ideas-cookie`), and today the only way to remove one is a hand-written Python line against `state/.secrets.json` (RUNBOOK 2.3, "Retired keys") or `python -m keyring del` on a keyring host. A stale credential that nothing reads is still a credential to leak. Acceptance: `python -m goldbot.ops.accounts delete <key>` removes the key from the OS keyring and from the plaintext fallback under `_fallback_lock` (file stays mode 600, the file is removed when empty); it never prints a value; an unknown key exits 1 with "not stored"; an MT5 key (`mt5-<account>`, `mt5-login-<account>`) of an enabled account in `config/accounts.yaml` is refused unless `--force`; tests for keyring, fallback, missing key, refusal and no-value-in-output. RUNBOOK 2.3 "Retired keys" then uses it.
