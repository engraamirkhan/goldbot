---
number: 264
rank: 89
title: Reject commits whose author email is not a noreply address
stage: Security · public repo hygiene
size: S
status: 'proposed 2026-11-14 (security review of fix/weekly-read-due) · accountable: implementer, release-manager · reviews: security-reviewer'
---
- **264. Reject commits whose author email is not a noreply address.** Value: the repo is public, and some past commits on main carry personal author emails and a local hostname. Those are already public, and rewriting history would not take them back. A guard stops a new clone, VM or VPS with an unset `user.email` from adding more. Acceptance:
  1. a pre-commit hook and a CI check fail any commit in the push range whose author or committer email does not end in `users.noreply.github.com` or equal `noreply@anthropic.com`;
  2. the check prints only the commit sha and the rule, never the offending email;
  3. a test covers one passing and one failing commit;
  4. RUNBOOK and the bootstrap scripts set `user.email` to the noreply address on every machine that commits.
