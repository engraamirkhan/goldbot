---
number: 187
rank: 11
title: Drop the retired I0-<instrument> and ZCD registrations once flat
stage: 'ops / config (follow-up of R133, profitability-2026-11-12, 2026-11-12)'
size: S
status: 'implemented, finishes itself (2026-11-14, branch feat/auto-gated-1114, R153): trigger: the engine de-registers each retired arm at the first reconciliation that finds the account flat in it (goldbot/engine/exploration_deregister.py; journalled exploration_arm_deregistered, aux.db exploration_arm_deregistered); the config rows stay, inert · accountable: implementer · reviews: trading-safety-reviewer'
---
- **187. Drop the retired I0-<instrument> and ZCD registrations once flat.** Value: R133 retires I0-USTEC, I0-XAUEUR, I0-DE40, I0-US500 (icm-probe), I0-US30, I0-XAUJPY (vantage-demo) and ZCD (icm-demo) in the engine and keeps them registered in config/accounts.yaml so their open positions keep every exit (a dropped registration is closed at once as `exploration_orphan`). Once each account shows no open position of them, drop the rows (profitability-2026-11-12.md section 4) to shorten the config. Acceptance, tests first: a test that the rows are gone from the named accounts and that no pot loses its last runnable arm; the brain's open positions of the ids are checked flat (journal or MT5) before the deploy and the check is recorded.
