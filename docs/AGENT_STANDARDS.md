# Agent quality standards

Every agent in `.claude/agents/` meets these shared standards in addition to the "Definition of done" in its own
file. The product owner accepts work against them; the release manager does not ship work that misses one.

## Shared standards (all agents)

| # | Standard | Evidence required |
| --- | --- | --- |
| S1 | **Evidence over assertion.** Every claim about code cites `file:line`; every claim about behaviour cites a test, a command output or an experiment. | Citations and verbatim output in the report |
| S2 | **Honest reporting.** What was not done, not verified, skipped or assumed is stated plainly. A pass is claimed only when observed. | "Not verified / left out" section |
| S3 | **Safety rails untouched.** RiskGate is the only path to an order; exits are never gated; the owner confirms entries (or eligible auto mode); demo until the phase gate; live only via `unlock_live` + phrase; trial budget and holdout respected. | Explicit statement per rail touched |
| S4 | **Public repo hygiene.** No secrets or identifiers (passwords, tokens, keys, MT5 logins, emails, Telegram ids, hosts/IPs) in code, config, docs, commits or issue comments. Per-server values go in `config/settings.local.yaml` (git-ignored) or the keyring. | Sensitive-string scan count (0) for anything pushed |
| S5 | **Scope discipline.** Stay inside the assigned files/topic; touch nothing another parallel agent owns; one logical change per commit. | `git diff --stat` matches the assignment |
| S6 | **Point-in-time correctness.** Any feature, label or join uses only data visible at decision time; proven by a truncation test, not just the pipeline check. | Test name |
| S7 | **Fail closed.** Missing or corrupt state on the order path blocks entries, never opens them; one bad item never stops a whole job or loop. | Test name |
| S8 | **Gates green.** `scripts/gates.sh` (add `--web` for frontend/API work) passes on a tree nobody is editing. | Verbatim gate table |
| S9 | **Docs in the same change.** HANDOFF bullet, TRACEABILITY row + counts, RUNBOOK when the owner's steps change, API contract regenerated after `goldbot/api` changes. | Files in the diff |
| S10 | **Owner decisions isolated.** Anything only the owner may decide (budget, instruments, going live, spending, deployment to servers, gate thresholds) is listed with options and a recommendation, never decided. | "Needs the owner" list |
| S11 | **Concise.** Reports within the length the brief sets; tables over prose where they help. | Word count |

## Review routing (who must sign off)

| Change touches | Required reviewers before release |
| --- | --- |
| `goldbot/engine`, `goldbot/risk`, `goldbot/execution`, approvals (`goldbot/telegram`, API decisions), exits | trading-safety-reviewer |
| `goldbot/research`, `goldbot/labels`, `goldbot/features`, costs, gates, trial registry | quant-reviewer |
| auth, accounts/keyring, deploy or bootstrap scripts, workflows, anything run as root | security-reviewer |
| everything | code-reviewer (correctness), test-engineer (gates) |
| any screen or Telegram message | ui-ux-designer |

A HIGH or CRITICAL finding blocks release until fixed and re-verified by the same reviewer.

## Acceptance scorecard (product owner)

Each delivered item is scored; it is accepted only with every row "yes".

| Check | yes / no |
| --- | --- |
| Every acceptance criterion in the backlog item has a passing test or shown evidence | |
| Agent's own Definition of done fully met (report shows each item) | |
| Shared standards S1-S11 met | |
| Required reviewers signed off; HIGH/CRITICAL findings closed and re-verified | |
| CI green on the PR; merged; main merged back into the branch | |
| Owner-visible behaviour change described in the PR | |

## Parallel work rules
- Each agent works in its own git worktree based on the tip of `claude/gifted-goldberg-lcb7pl`, on disjoint files.
- Python in a worktree: `env PYTHONPATH=$PWD /Users/aamirkhan/myproject/goldbot/.venv/bin/python` (check
  `goldbot.__file__` points at the worktree).
- Agents commit on their branch and never push; the integrator merges, runs the gates, routes reviews, and ships.
- Free lanes are refilled immediately from the ranked backlog (`docs/BACKLOG.md`).
