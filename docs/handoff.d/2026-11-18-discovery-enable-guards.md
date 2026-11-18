- 2026-11-18 (branch feat/discovery-enable-guards; R164, ADR 0090 Accepted): the quant and SRE GO conditions for
  enabling `discovery_nightly`, built; the job stays off (enable not before 2026-11-22 22:00 UTC, RUNBOOK "Discovery
  nightly on the brain", never on the same night as `arm_lifecycle`).
  - Alerts: a failed or killed night already FAILs `job:discovery_nightly` (health -> Telegram). New health check
    `discovery_last_run` (goldbot/ops/checks/discovery.py), only while enabled: WARNs, on NOTIFY_ON_WARN, when no night
    has succeeded (`ok`/`budget`/`over_budget` in aux.db `discovery_runs`) for 26 h; a job that ran disabled at its last
    slot is pending, not stale. Disabled: no check, no alert.
  - Memory: no per-child cgroup MemoryMax without a unit change (`Delegate=yes`), so the bound stays RLIMIT_DATA 4 GB +
    oom_score_adj 500; the job's health line warns past the SRE's 2,048 MB budget (`DISCOVERY_RSS_WARN_MB`). The child
    now also asks for the idle IO class (`bounded.idle_io`, ioprio_set on Linux x86_64/aarch64; a refusal never stops
    it).
  - `research.discovery.first_night_dry` (default true, quant condition (b)): an enabled night books its K to the
    ledger and the registry and writes its run record and report as any night, but writes no `candidate_arms` row; the
    report's "first night dry" section lists what it would have offered. A quant reviewer sets it false after reading
    that report; a withheld survivor is not offered later.
