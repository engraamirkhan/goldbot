# Weekly review 2026-11-14: decisions

## 697a881218a5313a: earlier send variant for stale signals. Declined.
obs:stale for the week ending 11-08 was 99% 1m random-control signals on icm-probe and vantage-demo, which run on two different MT5 hosts.
It was a refusal-labelling artefact, fixed by SRE fix 3 (2026-11-09).
Stale refusals fell from about 650-1,255 a day to 1-19 a day from 11-10.
The residual is about 10 a day, at a mean age of 15 s against the 10 s limit for 1m signals.
It is re-measured next week after the icm-demo rehost. No registry trial.

## ebcd6ef8a7dc2e28: review of the arm_budget refusals. Declined.
The 341 obs:arm_budget refusals (10-26..11-02) fell mostly on losing arms.
Their simulated label averages -0.067 R per strategy candidate (n=200, -13.4 R in total) and -0.095 R per control (n=179), so no likely-profitable trades were lost.
The check was removed by the 11-02 demo-limits ADR and has not fired since. No trial.

## H-81:Z4 and H-81:Z7 read_due: both reads void. Retirement stands.
The weekly review raised `read_due` for both units because each had passed 300 counted family fills.
H-81 section 4 registers the read as "at the 300th admissible family fill on ≥ 20 session days", and neither unit met the day count.
Both were also already retired under A5 (history-bounded entries, signed 2026-11-06), so no read was coming.
The evaluator recorded both reads as void in `config/arm_reads.yaml` (state R):

- **H-81:Z4**: the 300th fill came on 8 session days (< 20). The rule-only net was -0.177 R with an upper bound of -0.117 R, which is futile.
- **H-81:Z7**: the 300th fill came on 4 session days (< 20). Net was +0.064 R, P(>0) 0.77, upper bound +0.24 R (inconclusive). At 640 fills it was +0.012 R.

Neither read re-opens the unit. The A5 retirement of 2026-11-06 stands for both.
The weekly review now raises `read_due` only once the fill count and the registered session-day minimum (`min_session_days`) are both met.
It raises none for a unit that is already read or retired unread.

## Data gap: closed exploration rows without a fill price
The evaluator found 7 closed `exploration_trades` rows with a P&L but no fill price: 5 Z7V and 2 ZCV.
This is logged as the backlog item "exploration-fill-price-gap" and is not fixed here.
