# 0041: Entry mode account: the owner can hand entries back to each account's own setting

Status: accepted (program-director + trading-safety, delegated by the owner, 2026-10-19). Supersedes the second bullet
of ADR 0036's Consequences ("a control.json left at `propose` ... this is deliberate").

## Context
The owner's `/mode` (row A10) is one global override in `control.json` (`approval_mode`): `propose`, `auto` or None
(never set: each engine uses its account's own `approval_mode`, ADR 0036). Once the owner sent `/mode propose`, the only
way off it was `/mode auto <TOTP>`, which also needs the auto-mode evidence (100 decided proposals, no breach, no veto
difference). A demo account put on propose therefore could not return to automatic entries, which breaks ADR 0036
(demo enters automatically; real money keeps one-click confirmation; exits always automatic). Proposals on a demo
account with nobody clicking also produce no evidence, so the evidence gate could not be met either.

## Decision
- A third owner mode, `account` (`/mode account` on Telegram, `{"mode": "account"}` on `POST /api/automode`, the
  dashboard's "Use each account's setting"): clears the owner's override back to None. Both channels run the same
  code (`goldbot/telegram/automode.py` `mode_command`).
- Owner only (Telegram allow-list; dashboard `need("owner")` pinned to `auth.owner_email`), audited as `mode` with
  `to: account` and the previous mode. No TOTP and no evidence: it grants no authority the account's own configuration
  did not already have, and a code lockout cannot block it.
- `/mode auto` keeps its TOTP + evidence gate unchanged; `/mode propose` is unchanged.
- Real money cannot gain authority: the ADR 0036 lock is untouched (load-time account check, `check_start`, `locked` on
  every refresh, `permitted` at the order), so a live account, or a demo config on a terminal that does not report demo,
  stays on propose. The 30-day re-arm window and the 12% kill switch still force propose.
- The mode epoch (`mode_ts`) restarts on the change, like any other mode change: the auto-mode evidence counts afresh.

## Consequences
- A demo account the owner paused with `/mode propose` resumes automatic entries with one owner action, no code.
- The override now has three states and the dashboard heading says "Each account's setting" for None (it used to show
  "Propose-and-approve (engine setting)", which was wrong for a demo account configured auto).
- After a 12% kill switch with the override at None, the engine writes nothing to `control.json` (it resets only an
  owner `auto`); the account halts, the re-arm window forces propose for 30 days, then a demo account returns to its
  configured auto, as ADR 0036 already specified.
- Changing which accounts may enter automatically still needs a new owner decision and ADR; the lock is code.
