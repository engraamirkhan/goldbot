- 2026-11-13 (branch feat/gold-cost-session-split; BACKLOG 146; R137; prereg-2026-11-07-catalogue-screen section
  14a): the catalogue sim prices a gold trade at the cost-of-record cell of its fill instant's session, the dearer of
  the two brokers (`costs.gold_session_rt`, `costs.gold_session`; the calendar's city-clock sessions). A cell on fewer
  than 30 fills reads no lower than its broker's pooled cell, a missing one reads the pooled cell, and no session is
  charged below the old flat $0.166. cor-2026-10-31.2: asia $0.204 (ICM, 309 fills; Vantage asia $0.158), london
  $0.293 (Vantage, 44 fills, 90% CI 0.158-0.45; ICM $0.164), newyork $0.263 (Vantage, 69 fills; ICM $0.166). The thin
  hours (New York 17:00 to the Tokyo open), which no cost-of-record cell holds, pay the higher of the dearest session
  and every broker's pooled figure ($0.293). The version is pinned by catalogue_costs.yaml `deciding.cost_of_record`;
  any other version raises MissingCost. The risk floor and the S-detector range filter use the decision instant.
- The dearer-broker max is the screen basis only: every summary also reports `net_icm_basis` (gold on ICM's own
  cells; `costs.gold_basis_gap`), never gated. H-90 and deployment price each broker on its own cells, not the max.
  The EVAL-2 deciding cost ($0.230) and RiskGate are unchanged. The basis applies only to catalogue runs after this
  commit (prereg section 14a); earlier reads keep $0.166.
- Research goldens that moved (synthetic fixtures; no stored screen result was re-run):
  - fill-rule and fine-fill costs: the flat bars open at 21:00 UTC in January, inside New York's EST session ($0.263);
  - the sprint3000 parity fixture: 22:00 UTC fills fall in the thin hours ($0.293);
  - risk-floor fixtures: the floor rose from 0.498 to 0.789 (0.879 in the thin hours), so stop distances were widened.
  Every catalogue gold net result moves down, and the 3 x RT risk floor rises.
- **After merge, every eval2 self-test report is stale:** the harness hash now covers config/cost_of_record.yaml.
  Run `scripts/eval2_selftest.py` at the merge commit before any EVAL-2 read.
