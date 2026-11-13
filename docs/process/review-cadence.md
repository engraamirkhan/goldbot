# Review cadence: who clears which review issue, and when

The system writes its own reviews; agents act on them. There are no Claude crons (usage limits stop them, see
`no-auto-restart-after-limit`) and nothing waits on the owner: the brain's scheduler opens the issues, and the
program-director clears them at the start of every session.

## The program-director's first step, every session

Before planning new work, list the open review issues and clear them:

```
gh issue list --label review --state open
gh issue list --label review-monthly --state open
```

For each issue: run the named reviewers as lanes (below), turn every flag into an owned action (a fix lane, a
BACKLOG item, or a decision record with the reason it is accepted), comment the outcome on the issue, and close it.
An issue is closed only when every flag has an owner and a date; a flag is never closed as "looked at".

| Label | Opened by | Cadence | Reviewers (lanes) |
|---|---|---|---|
| `review` | the weekly review job | weekly | performance-analyst, research-director |
| `review-monthly` | the `monthly_review` job (goldbot/ops/monthly_review.py, R142) | day 1 of each month, UTC | finance-auditor, CFO, CTO |

## What the monthly issue carries, and what each reviewer owns

- **finance-auditor**: the per-account balance tie-out (RC-9). An `UNRECONCILED` account is a finding until the
  difference is explained to the deal (`python -m goldbot.ops.ledger show <account>` and
  `python -m goldbot.ops.monthly_review --month YYYY-MM` on the brain hold the amounts). It is never waived as rounding:
  the tolerance already covers rounding.
- **CFO**: spend (agents + hosting) against the budget in docs/finance, and the month's returns against ADR 0004.
  Spending, sizing and risk-limit changes stay owner-only: the CFO recommends.
- **CTO**: the reliability scorecard (jobs failing or overdue, health alerts, market minutes down) and the trial
  budget used (K). A job failing at review time gets a fix lane the same session.

## Rules

- The public issue never carries an MT5 login, a host or a balance (the job scrubs it); comments follow the same rule.
- No issue was opened (no `github-token` on the brain, or the post failed): the text waits in
  `state/aux/monthly_review/outbox/<month>.md` and the next run posts it; the owner's Telegram message went out anyway.
- Reviewers are read-only; fixes go through the normal lane flow (docs/AGENT_STANDARDS.md).
