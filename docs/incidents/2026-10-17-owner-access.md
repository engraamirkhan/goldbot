# Incident: the owner could not reach or sign in to the dashboard after the 17 Oct deploy

Status: resolved — the owner signed in at ~07:55 UTC after the reset and a fresh setup. Program director accountable. States and timings only.

## What happened (UTC)

- 05:23 brain deployed to main 01e70a7; 05:35 engine re-enabled; the program director reported "deploy complete".
- The dashboard had **no public address**: the Cloudflare tunnel in the RUNBOOK checklist (2.7) had never been set up,
  and the "deploy complete" report did not include "the owner can open it".
- ~06:30 the owner asked for the URL. Only an SSH port-forward was available. The quick tunnel was blocked twice by the
  auto-mode classifier; it ran on the third attempt after the owner chose the free option, giving a random
  `*.trycloudflare.com` address (temporary).
- The owner created the account on the setup screen, then could not sign in ("invalid credentials"). The rules that
  bit — each authenticator code works once (the setup-time code is spent), lockout after 5 failures — were not explained
  before the first attempt.
- The reset (clear the auth tables, mint a new setup code) was blocked by the classifier; a copy of the reset command
  picked up markdown indentation and failed **after** stopping the API, so the dashboard was down for several minutes.
  The program director then ran the reset on the owner's instruction; the API is up and the setup screen is back.
- The owner's stated expectation — a one-time code sent to the owner's email — does not exist; only an email sign-in
  *link* exists, unconfigured, and it still asks for the authenticator code.

## Why

1. "Done" for a deploy was defined as services stable, not as the owner reaching and using the product. (PD, CTO.)
2. The RUNBOOK's owner-facing setup (tunnel, first run, sign-in rules) was never walked through from the owner's side.
3. Commands handed to the owner were multi-line; a copy from markdown broke them.
4. The sign-in design was built to the security design (ADR 0013) without the owner's everyday use being checked.

## What changes

- **Definition of done for any deploy or owner-facing change (effective now):** the owner can open it at its address and
  complete the first action (sign in / approve) — verified from outside the servers, recorded in the deploy note.
- Commands given to the owner are single-line and tested by pasting them from the rendered message.
- A fixed address: owner picks free subdomain or own domain; until then the quick tunnel stays up (unit
  `goldbot-quicktunnel`), and its address is read from the journal when it changes.
- Sign-in by a one-time code to the owner's email: BACKLOG 77 (spec docs/ux/sign-in-email-code.md), security plan review
  first, then built ahead of everything else owner-facing.
- The setup and login screens say the two rules in plain words: a code works once; five misses lock for 15 minutes
  (ui-ux follow-up on BACKLOG 77).
- Classifier blocks on operational commands are reported to the owner in one line with the exact command, the first
  time, not after a retry.

Follow-ups owned: PD (this file, #34 note), CTO (definition of done in docs/AGENT_STANDARDS.md), ui-ux + security
(BACKLOG 77), sre (fixed address once the owner chooses).
