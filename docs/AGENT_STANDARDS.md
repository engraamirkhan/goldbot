# Agent quality standards

Every agent in `.claude/agents/` operates at principal level in its domain ("Principal-level expectations" in its
file: owns outcomes, sets the standard, raises risks unasked, decides with evidence and records decisions in
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
| S12 | **Persisted state survives a rollback** (ADR 0010). Every new field on a persisted record has a default that is safe when missing: an older release drops the field on rewrite, and the newer one must then read the default correctly. Persisted state is read with `goldbot.base.load_record` / `load_record_json` (or `RecordRepo(..., tolerant=True)`); only the strict allowlist (credentials, `Control`, `Settings`, bridge order requests) reads with `forbid`. A new persisted model adds a case to `tests/test_persistence_compat.py`. A new restriction is encoded so an older release fails closed: a new value of an existing `Literal` (an older release rejects it), never a new boolean or optional field (an older release drops it and trades without the restriction). A new top-level table in a state file that carries client ids or broker orders (an older release does not read it, only warns) needs a rollback story reviewed by the trading-safety reviewer before it ships. **Bridge responses too** (OrderResult, Position, SymbolInfo, AccountInfo, Tick, BrokerTerms, TradingSession; read tolerantly by the brain): a new field never changes the meaning of an existing one; a non-fill, partial or pending outcome is expressed through `ok=False`, `retcode`, `filled_lots` or `position_id`, never through a new field alone; a new restriction is a new `Literal` value of an existing field or a change to an existing boolean; any change goes to the trading-safety reviewer and bumps `bridge.API_VERSION`. | Default named per new field; compat case or allowlist entry |

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
The `program-director` leads delivery (roadmap, waves, integration, risk register, owner reporting); the
`product-owner` owns what is built and accepts it. The `research-director` leads the research team
(strategy-researcher, performance-analyst, evaluator) toward the owner's north-star: £50 or more on days the market
offers opportunity, less or nothing when it does not (ADR 0004); it owns the research agenda, trial budget and the
monthly scorecard. All three report to the owner.

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

A HIGH or CRITICAL finding blocks release until fixed and re-verified by the same reviewer.

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

## Parallel work rules
- Each agent works in its own git worktree based on the tip of `claude/gifted-goldberg-lcb7pl`, on disjoint files.
- Python in a worktree: `env PYTHONPATH=$PWD /Users/aamirkhan/myproject/goldbot/.venv/bin/python` (check
  `goldbot.__file__` points at the worktree).
- CPU is shared: when several agents run at once, run pytest with `-n 4` (or `GATES_WORKERS=4 scripts/gates.sh`), never
  `-n auto`, so parallel lanes do not starve each other (load hit 106 on 12 cores with every agent on `-n auto`).
- Agents commit on their branch and never push; the integrator merges, runs the gates, routes reviews, and ships.
- Free lanes are refilled immediately from the ranked backlog (`docs/BACKLOG.md`).

## Test rules
- Stop and exit tests must prove the broker did not do the work: the broker's stop is far away (or its modify is
  rejected), the engine's is tight, and the test asserts the position is still open after the broker saw the price and
  before the engine pass (`tests/test_engine_clock.py::_StopStuck`).
