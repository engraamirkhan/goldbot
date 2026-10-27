# 0050: Trades views: money always visible, arm results blinded until the read

Status: accepted with the quant-reviewer's tightenings (principal product designer, 2026-10-27)

## Context
The owner asked for a trades experience that shows "valuable and complete" information, including per-strategy net R,
hit rate, confidence intervals and comparison with controls (docs/design/trades-ux-2026-10-27.md). The exploration
arms are pre-registered (H-61) with one read per arm at its 300th admissible trade and a binding blinding rule
(docs/ux/daily-learning-report.md §3.4, quant review 2026-10-26): no per-arm mean, sum, rank, sign, share or
comparison with the random baseline before that read. A leaderboard of running per-arm results would be an interim look
seen daily by the owner and agents, inviting optional stopping and tinkering. At the same time the owner must always
see his money: what is at risk now, what each account made, and what any single trade did.

## Decision
1. Money is never withheld: account P&L, every open trade's risk and P&L, a single trade's own result in its detail
   view, and lab/style totals (at least 3 arms, `labs.MIN_ARMS_FOR_TOTAL`).
2. Before an arm's read, no per-arm aggregate (sum, mean, hit rate, CI, rank, share, versus control) is computed for
   any client. The API omits the fields; blinding is enforced server-side, not by the UI.
3. Any list, filter or totals line whose set holds unread arms but fewer than 3 of them (one unread arm, or a family
   with fewer than 3 unread variants, including a partly-read family with one read and one unread timeframe) returns
   rows with `blinded: true` and the result fields null, and its totals carry count, costs and n only; sorting by
   result is disabled while unread arms are in view.
4. The strategy board orders families by state and progress to the read, never by result, until the read. Its band
   shows "running" or "retired at its stop" only; "near its stop" (derived from the running loss) is never shown
   before the stop has fired. After the
   read it shows the recorded verdict and the frozen read figures; later figures are labelled "since read".
5. Engine strategies and shadow models (not pre-registered arms) show full figures with n and a 90% CI.

## Consequences
- The owner gets progress (n of 300, ETA, band, anneal state) instead of a running per-arm score; the read stays valid.
- One payload-level leak test (seeded distinctive per-arm R and shares must not appear in any `/api/trades*` or
  `/api/strategies/board` response) joins the digest's blinding test; it includes a filter on a partly-read family
  (one read, one unread variant) and the totals of a 2-unread-arm set.
- Mixed lists still show single trades' results; a determined reader could tally them by hand. Accepted: the rule
  targets daily aggregate looks; if the quant reviewer rules otherwise, mixed lists drop exploration results and keep
  money at the lab level only.
- Revisit with a new pre-registration that changes the read rule.
