- 2026-11-18 (branch feat/discovery-nightly; R159, ADR 0090): `discovery_nightly`, the brain's own nightly strategy
  search (owner directive 2026-11-18, research director's build 4). Off until a host sets `research.discovery.enabled`
  and `bars_path` (RUNBOOK "Discovery nightly") and the research director sets `k_cap_quarter`; quant-reviewer and
  sre GO first.
  - One bounded child (`goldbot/ops/bounded.py`) on the 2-vCPU ARM brain (one core for the child, one for the
    engines): nice 15, oom_score_adj 500, RLIMIT_DATA 4 GB, one BLAS thread, no new timeframe after `budget_s`
    (5400 s), killed at budget + 300 s, 01:00-03:10 UTC, clear of every other daily job; one run at a time
    (state/autodisc/discovery.lock). The result carries elapsed_s and the child's peak RSS; health warns past 80 % of
    budget_s. Enabled without bars fails the job instead of skipping (ADR 0083's lesson); every unfinished child
    leaves a `killed`/`failed` row in `discovery_runs`.
  - G2 on 1h/4h/1d, explore 2010-2017 only, bars only through `load_research_bars` (holdout sealed). Every spec, null
    spec and plateau neighbour gets the ADR 0085 power verdict from its signal counts BEFORE it is ledgered
    (`gen_gp.Admit`): refused, it costs no K and the search redraws it at sampling; none is admitted once `autodisc:G2`
    has been read on TEST-OLD. Two budget checks: one registry row per timeframe against the trial budget
    (`assert_budget`), and the quarter's discovery K plus the night's planned K against
    `research.discovery.k_cap_quarter`, which ships at 0 (every night refused) until the research director sets it.
    Costs: cost of record v3 per (session, exit) cell in bps of 4,000 $/oz on mid labels, so the spread is charged
    once; swap is the settings prior.
  - Grammar-node priors (`GPConfig.leaf_priors`, bounded 0.5-2.0) from demo labels decided from 2026-10-01 by family
    and session, and from each discovered arm's forward labels on its spec's nodes; the G0 null uses the same priors.
  - Survivors that can run live and that the forward demo window over the learning kill rule (45 days or 300 fills)
    can judge are offered. Under ADR 0085's parameters that window cannot judge any spec (n_eff <= 128 < 200),
    so no candidate arm is offered until the quant rules on it. Each judged candidate reports its admitted plateau
    neighbours. Offered rows go to `candidate_arms` in the discovery ledger's database
    (`goldbot/research/autodisc/handoff.py`, `pending()` for a hot-load path); registration stays
    `ops/autodisc_register.py`. Run record -> aux.db `discovery_runs` (migration 37) and the ledger's `nightly_runs`;
    report -> state/autodisc/discovery-<night>.md. No confirm read is opened (ADR 0085's 4 batches a year).
  - Outcome-blind reading on the real bars: power admits ~8 % of random G2 specs at 1h, ~5 % at 4h; no catalogue card
    with a stated count passes (31 refused, 0 started).
