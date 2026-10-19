# Agent quality standards

Every agent in `.claude/agents/` operates at principal level in its domain, with at least 15 years of hands-on experience in
it, the last several at principal level ("Principal-level expectations" in its file: owns outcomes, sets the standard, raises risks unasked, decides with evidence and records decisions in
`docs/decisions/`, multiplies the team, escalates owner-only decisions). Every agent meets these shared standards in addition to the "Definition of done" in its own
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
| S9 | **Docs in the same change.** HANDOFF bullet, TRACEABILITY row, BACKLOG status, RUNBOOK when the owner's steps change, API contract regenerated after `goldbot/api` changes. HANDOFF, TRACEABILITY and BACKLOG: add a fragment (`docs/handoff.d/`, `docs/traceability.d/`, `docs/backlog.d/`; new ids from `scripts/docs_assemble.py new-handoff / new-row R / new-item`) and run the assembler (`python scripts/docs_assemble.py`); never edit the rendered files (ADR 0012). | Files in the diff; `docs-assemble` hook passes |
| S10 | **Owner decisions isolated.** Anything only the owner may decide (budget, instruments, going live, spending, deployment to servers, gate thresholds) is listed with options and a recommendation, never decided. | "Needs the owner" list |
| S11 | **Concise.** Reports within the length the brief sets; tables over prose where they help. | Word count |
| S12 | **Persisted state survives a rollback** (ADR 0010). Every new field on a persisted record has a default that is safe when missing: an older release drops the field on rewrite, and the newer one must then read the default correctly. Persisted state is read with `goldbot.base.load_record` / `load_record_json` (or `RecordRepo(..., tolerant=True)`); only the strict allowlist (credentials, `Control`, `Settings`, bridge order requests) reads with `forbid`. A new persisted model adds a case to `tests/test_persistence_compat.py`. A new restriction is encoded so an older release fails closed: a new value of an existing `Literal` (an older release rejects it), never a new boolean or optional field (an older release drops it and trades without the restriction). A new top-level table in a state file that carries client ids or broker orders (an older release does not read it, only warns) needs a rollback story reviewed by the trading-safety reviewer before it ships. **Bridge responses too** (OrderResult, Position, SymbolInfo, AccountInfo, Tick, BrokerTerms, TradingSession; read tolerantly by the brain): a new field never changes the meaning of an existing one; a non-fill, partial or pending outcome is expressed through `ok=False`, `retcode`, `filled_lots` or `position_id`, never through a new field alone; a new restriction is a new `Literal` value of an existing field or a change to an existing boolean; any change goes to the trading-safety reviewer and bumps `bridge.API_VERSION`. **One exception, recorded:** a new response field may carry a restriction only if an unknown or missing value fails closed on the brain AND the bridge enforces it independently; that requires a protocol bump (`API_VERSION`, and `MIN_COMPATIBLE` so an older peer is named incompatible). The precedent is `AccountInfo.margin_mode` (protocol v4): the brain refuses to start unless it reads `hedging` (missing = refused), and the bridge's own `verify_terminal_account` refuses at its start and on every order. | Default named per new field; compat case or allowlist entry |
| S13 | **Owner-verified done.** For any deploy or owner-facing change, the owner can open it at its fixed address and complete the first action (sign in / approve), checked from outside the servers and recorded in the deploy note. Owner-facing commands are single-line and pasted from the rendered message before handing over. A classifier block on an operational step is reported to the owner in one line with the exact command the first time. Also in the release-manager's, sre's and program-director's Definition of done (incident 2026-10-17). | Deploy note names the address and the first action completed |
| S14 | **Token budget.** Follow docs/process/token-efficiency.md: model tier per role, reviewers sized to risk, narrow tests in lanes (one full gates run per release candidate), briefs with file:line, hand-backs at most 150 words verdict first, no CI polling. Never relaxes S1-S13 or the HIGH/CRITICAL re-verify. | Lane brief names tier and test scope; word count |

## Workflow security (anyone editing `.github/workflows/`)
- Pin every action to a full 40-hex commit SHA with the version as a trailing comment
  (`uses: actions/checkout@<sha> # v5.1.0`). Resolve it with `gh api repos/<o>/<r>/git/ref/tags/<tag>` (dereference
  an annotated tag with `.../git/tags/<sha>`); never guess or copy a SHA from memory. Dependabot bumps them weekly.
- Top-level `permissions:` is `{}` or `contents: read`; write scopes are granted per job and commented with why.
- Never interpolate `${{ github.event.* }}`, `${{ inputs.* }}` or matrix values into `run:` or an inline script:
  pass them through `env:` and quote `"$VAR"`.
- `actions/checkout` sets `persist-credentials: false` unless the job pushes with git.
- Run `actionlint` (with shellcheck installed) before committing a workflow change; it must be clean.

## Root-run tooling (anyone editing `goldbot/ops/linux/` or a runbook that uses `sudo`)
- Anything root installs or executes is fetched by root from a source outside the service user's control (GitHub
  over HTTPS, root's own clone in a root-only directory) and hash-checked against a value computed off-box (the
  owner's own clone). The service user's tree (`/opt/goldbot`) is never a source for root.
- A hash of the service user's git object store is not verification: that account owns `.git` and can swap a blob
  (`git replace`, rewritten objects), so `sudo -u goldbot git show ... | sha256sum` proves nothing.
- No runbook runs root `chmod`, `chown`, `cp`, `rm`, `mv`, `tee` or `install` on a path under `/opt/goldbot`; use
  `sudo -u <service user>`. Root-run scripts read and write service-user paths only through `sudo -u`.
- Root-run wrappers write into a service user's home only via `sudo -u <user>` (for example appending to
  `~/.ssh/authorized_keys`), after validating the input as exactly one line of the expected shape.
- In `goldbot/ops/linux/*.sh`, a file command (`chmod chown cp mv rm install tee cat`) or redirection on `$ROOT`,
  `/opt/goldbot`, `/home/mt5` or `/var/lib/goldbot` starts with `sudo -u` (or `as_owner`); the only exception carries
  `# root-ok: <reason>` on the same line (creating `/opt/goldbot` before the service user owns anything).
- A manual root script ignores SIGPIPE and SIGHUP (`trap '' PIPE HUP`) before it starts changing services, so a
  dropped session never leaves services stopped half-way; its durable record is a root-owned log, not the terminal.
- Enforced by `tests/test_root_tooling_rules.py` (runbooks and `goldbot/ops/linux/*.sh`, comments included); the
  security reviewer signs off every change to a root-run script before its root copy is reinstalled.

## Leadership
The `program-director` leads delivery (roadmap, waves, integration, risk register, owner reporting); the main Claude
session acts as program-director and answers for its failures (an unchased owner step, idle lanes, stale items). The
`product-owner` owns what is built and accepts it. The `research-director` leads the research team
(strategy-researcher, performance-analyst, evaluator) toward the owner's north-star: £50 or more on days the market
offers opportunity, less or nothing when it does not (ADR 0004); it owns the research agenda, trial budget and the
monthly scorecard. The `finance-auditor` owns the money picture and the independent audit: the monthly GBP ledger
(every trading and running cost, reconciled to the broker), budget and break-even after free trials end, and the
monthly audit of profitability, rule compliance, costs and learning, with ranked recommendations to the
research-director and program-director. All four report to the owner.

Two executives sit above the principals (owner, 2026-10-16). The `cto` owns technical strategy and the platform
roadmap: architecture direction (the architect reports to it), build-vs-buy, technical debt and its budget, the
engineering quality bar (gates, CI, test strategy), reliability and security posture with the sre and
security-reviewer, technical risks and ADRs, and capacity and tooling; it arbitrates technical disagreements between
principals and edits only `docs/decisions/`, `docs/ROADMAP.md` and `docs/process/` (code goes to the implementer).
The `cfo` owns the money strategy: capital allocation and the growth plan within ADR 0004, the operating budget,
break-even and ROI of every recurring cost and of research spend, the recommended risk appetite, UK tax and
reporting readiness as owner flags, and the monthly finance review; the finance-auditor reports to it. **Decision
forum** (owner delegation 2026-10-16): routine decisions are made by the program-director and the product-owner,
with the CTO on technical matters and the CFO on anything with a money effect; owner-only matters are escalated
with a recommendation. Anything that spends money, changes a paid plan, changes sizing or a risk limit, or relates
to going live stays owner-only; the CFO prepares the numbers and never loosens a RiskGate limit.

## Review routing (who must sign off)

| Change touches | Required reviewers before release |
| --- | --- |
| `goldbot/engine`, `goldbot/risk`, `goldbot/execution`, approvals (`goldbot/telegram`, API decisions), exits | trading-safety-reviewer |
| `goldbot/research`, `goldbot/labels`, `goldbot/features`, costs, gates, trial registry | quant-reviewer |
| auth, accounts/keyring, deploy or bootstrap scripts, workflows, anything run as root | security-reviewer |
| everything | code-reviewer (correctness), test-engineer (gates) |
| any screen or Telegram message | ui-ux-designer |
| goldbot/ops/linux, systemd, deploy, backups, health/alerts | sre |
| goldbot/data, data workflows, release loaders, feature inputs | data-engineer |
| architecture or platform changes (module boundaries, protocols, persisted state, deploy topology, CI and gates), any new dependency or service | cto |
| anything that changes costs, position sizing or paid services | cfo (owner decides spend, paid plans, sizing and limits) |

**Model tier (S14):** Opus for trading-safety-reviewer, quant-reviewer, architect, cto, cfo; Sonnet for code-reviewer, security-reviewer, sre, data-engineer, test-engineer, implementer; Haiku for ui-ux-designer copy checks, docs-only review and status polling. A Sonnet reviewer that finds a HIGH escalates the re-check to Opus. LOW docs/test-only changes may use one combined reviewer prompt.

A HIGH or CRITICAL finding blocks release until fixed and re-verified by the same reviewer. Reviews follow the
one-pass rule under "Accountability and cadence"; a requested review starts within 4 h.

## Acceptance scorecard (product owner)

Each delivered item is scored; it is accepted only with every row "yes".

| Check | yes / no |
| --- | --- |
| Every acceptance criterion in the backlog item has a passing test or shown evidence | |
| Agent's own Definition of done fully met (report shows each item) | |
| Shared standards S1-S12 met | |
| Required reviewers signed off; HIGH/CRITICAL findings closed and re-verified | |
| CI green on the PR; merged; main merged back into the branch | |
| Owner-visible behaviour change described in the PR | |
| Accountable role and due date recorded; met, or re-dated with the reason and escalated within 24 h | |

## Parallel work rules
- Each agent works in its own git worktree based on the tip of `claude/gifted-goldberg-lcb7pl`, on disjoint files.
- Python in a worktree: `env PYTHONPATH=$PWD $REPO/.venv/bin/python` (check
  `goldbot.__file__` points at the worktree).
- CPU is shared: when several agents run at once, run pytest with `-n 4` (or `GATES_WORKERS=4 scripts/gates.sh`), never
  `-n auto`, so parallel lanes do not starve each other (load hit 106 on 12 cores with every agent on `-n auto`).
- Agents commit on their branch and never push; the integrator merges, runs the gates, routes reviews, and ships.
- Free lanes are refilled immediately from the ranked backlog (`docs/BACKLOG.md`).

## Accountability and cadence

Why (review of 2026-10-16, `docs/process/scorecards/2026-W42.md`): the 13 Oct demo deploy never happened and nobody
chased it for three days; reviewed work sat five days off `main`; `macro-v1` was published on 13 Oct while the docs
still said it did not exist; five of eight Wave 1 lanes never started. None of those was a quality problem. Each
was an item that nobody owned by name and nobody checked.

**One accountable role per item.** Every BACKLOG item, ROADMAP milestone and lane, HANDOFF next step and dated target
names exactly ONE accountable role (`accountable: <agent>`) and a due date. Owner-only steps name `owner` and also
the role that chases them (`chaser: program-director` by default). An item without both is not ready and is not
started. Other roles may contribute; one role answers for it.

**Late rule.** A role that sees an item it owns late, or due within 48 h with no clear path, tells the
program-director within 24 h: what slipped, why, the new date, and what would unblock it. The program-director
puts it on issue #34 for the owner the same day, with options and a recommendation, when it needs the owner or moves
a milestone. A slipped date is re-dated with the reason written beside it, never carried forward silently. An
owner-run step past its date is chased the same day. "Waiting on the owner" is a status with a chase date, not an
excuse.

**Recurring duty.** Every agent file has an `## Accountability` section: what the role checks, how often, and what it
does when something it owns is late. Each check is short and ends in an action (do it, re-date it, or escalate).

**Cadence** (the main Claude session acts as program-director and runs all three):

| When | Who | What |
| --- | --- | --- |
| Every 4 h while work runs | program-director | Light check: stalled or idle lanes (refill per the adaptive lane policy); any lane past 2 review rounds; Definition-of-done gaps in finished work; items late or due within 48 h, each chased; owner steps due today, chased. |
| Daily | an independent reviewer (fresh agent, read-only on code) | Full review: grades all 20 roles (18 principals plus the cto and cfo) over the last 24 h from git, PRs, #34 and the docs; writes `docs/process/scorecards/<YYYY-MM-DD>.md`; proposes edits to the role file of any role graded C or below. |
| Weekly | program-director | Roll-up `docs/process/scorecards/<YYYY>-W<NN>.md` and a short summary on #34: per role, items owned, delivered, late and blocked, grade, and the fix for each C or below. |

Scorecards are one file per day or week (no shared file, so no merge conflicts; ADR 0012's reason). In-session
schedulers expire after 7 days or when the session ends, so **the program-director re-arms all three at the start
of every session** and says so in its first #34 note of the day. A session that starts without them has not started.

**Delivery rules** (speed without lowering the bar; every review, gate and safety rail stays):
- **WIP limit on unshipped work.** Reviewed work on the integration branch goes to a PR within 24 h of its first
  merge there. More than 24 h of reviewed, unshipped work stops new builder lanes until the PR is open.
- **Reviewer queue limit.** No more than 2 finished-but-unreviewed items per reviewer; above that, start reviewer
  lanes, not builders.
- **One-pass reviews.** A reviewer returns every finding in one ranked pass (HIGH/CRITICAL, then MEDIUM, then LOW),
  not one at a time; the builder fixes all of them in one commit; the re-verify checks only those findings plus the
  diff that fixed them. A third round goes to the program-director, who decides scope with the reviewer.
- **Automate owner steps.** An owner-run step is the last resort, limited to what legally or physically needs the
  owner (sign-ups with a card or MFA, CLI logins, typing a secret into a hidden prompt, a TOTP scan). Everything else
  is Claude-run, scripted and idempotent (memory "claude-runs-deploys"). Each owner step that remains has a date, a
  check-in time and a chaser.
- **Dispatch, don't list.** When a role writes "X must run by date D" and X is a workflow or command it may run, it
  runs X and records the result. Listing a task for someone else is allowed only when the role cannot do it.
- **Status truth within 24 h.** When a fact changes (a release is published, an item merges, a date slips), the
  accountable role updates the doc that states it within 24 h. The daily review flags any status that contradicts
  git, `gh release list` or `gh run list`.
- **Stale-item sweep, daily.** Any owned item untouched for 3 days, any open PR older than 48 h (Dependabot
  included), and any branch with unmerged reviewed work is listed in the daily scorecard with its accountable role.

## Test rules
- Stop and exit tests must prove the broker did not do the work: the broker's stop is far away (or its modify is
  rejected), the engine's is tight, and the test asserts the position is still open after the broker saw the price and
  before the engine pass (`tests/test_engine_clock.py::_StopStuck`).
