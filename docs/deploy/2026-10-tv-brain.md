# TradingView on the brain: pull + webhook rollout (ADR 0030)

SRE, 2026-10-18. Claude runs it; the owner clicks. Procedure, checks and rollback: RUNBOOK 2.7d (steps T1-T5, W1-W5).
This note holds the plan, the owner steps and the record.

**When:** after the brain deploy that brings branch `claude/gifted-goldberg-lcb7pl` (tv-pull, `accounts set --stdin` /
`export`, `state/tv_pull.lock`, the `webhook` / `tv-pull` heartbeats) is deployed and healthy. Target **Tue 20 Oct
2026**; latest **Fri 23 Oct 2026** (before the DST gap week). Late: the program-director hears within 24 h.

**Not before:** the deploy is healthy (`goldbot run health`, last line of `/var/lib/goldbot-deploy/deploys.jsonl`
`deployed`). Never on a Friday after 20:00 UK or at a weekend reopen: a failed restart there is a crash loop nobody
watches.

**Order** (each unit only after what it needs, or a crash loop fails the next deploy's 90 s window and rolls it back):

| Step | What | Who | Owner action |
|---|---|---|---|
| T1 | Cookies Mac Keychain -> brain secret store, by pipe | Claude | click Allow (never Always Allow) if macOS asks about the Keychain |
| T2 | `tv_pull.enabled: true` in `settings.local.yaml`, settings still load | Claude | none |
| T3 | one `tv-pull sync` by hand (backfill) | Claude | none |
| T4 | install + enable `goldbot-tv-pull` | Claude | none |
| T5 | checks: active, 0 restarts, live bars, `heartbeat:tv-pull` and `tv_pull` ok | Claude | none |
| W1 | new webhook secret on the Mac (Keychain), only if none exists | Claude | click Allow (never Always Allow) if asked |
| W2 | secret Mac -> brain, by pipe | Claude | none |
| W3 | install + enable `goldbot-webhook`, loopback `/health` | Claude | none |
| W4 | Tailscale Funnel on `/tv` | Claude | sign in (if Tailscale is new on the brain) and approve Funnel: two links |
| W5 | outside check (`403` from the Mac), alerts created, first `200` | owner + Claude | create the alerts in TradingView (2.7b step 2), paste the secret from the clipboard |

No value (cookie, secret, address, account id) is ever typed into chat, an issue, a commit or this file. The Funnel
address stays in the Terminal and TradingView.

**Known open points**

* Funnel passing TradingView's address in `X-Forwarded-For` is not verified. W5 shows it: every alert `403 ip not
  allowed` -> `GOLDBOT_TV_ENFORCE_IP=0` drop-in (RUNBOOK 2.7d W5); the 256-bit secret then authenticates alone.
* Memory and disk budgets are targets, not measured yet: record them after 24 h below.
* One puller per TradingView account (the brain). The Mac probe runs only by hand, outside the pass minutes; its
  requests are not in the brain's budget. A cross-host guard is not possible from the code; this rule is it.
* Same session used by the browser and the brain from two addresses: TradingView may end it. Detection: `tv_pull`
  expired WARN + one notice; recovery: sign in, T1.

**Record** (fill in on the day; no secrets, no addresses)

| Item | Result |
|---|---|
| Release deployed (SHA, public) | |
| T3 `tv-pull sync` result line | |
| T5 checks (active / NRestarts / heartbeat / tv_pull) | |
| W3 loopback `/health` | |
| W5 outside `403` from the Mac (date, time UTC) | |
| First alert `200` (indicator, tf) | |
| `X-Forwarded-For` through Funnel: passed / enforce off | |
| After 24 h: RSS tv-pull / webhook (MB) | |
| After 24 h: `tv_indicators` growth (MB/day) | |
| After 24 h: requests per day | |
