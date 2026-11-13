- 2026-11-13 (branch feat/gold-cost-session-split; BACKLOG 146; R137): the catalogue sim prices a gold trade at the
  cost-of-record cell of its fill instant's session, the worse of the two brokers (`costs.gold_session_rt`,
  `costs.gold_session`; the calendar's city-clock sessions, the hours after the New York close read as Asia). A cell on
  fewer than 30 fills reads no lower than its broker's pooled cell, a missing one reads the pooled cell, and no session
  is charged below the old flat $0.166. cor-2026-10-31.2 gives asia 0.204, london 0.293, newyork 0.263. London and New
  York rise more than the item expected because Vantage's cells (44 and 69 fills, entry slip +0.135 and +0.097) are
  dearer than ICM's. The table version is pinned by catalogue_costs.yaml `deciding.cost_of_record`; any other version
  raises MissingCost. The risk floor and the S-detector range filter use the decision instant. The buy-and-hold book
  uses its first mark (23:00 UTC, Asia). A call with no instant pays the dearest session.
- Research goldens that moved (synthetic fixtures; no stored screen result was re-run): fill-rule and fine-fill costs
  (flat bars open at 21:00 UTC in January, inside New York's EST session: 0.263); the sprint3000 parity fixture (22:00
  UTC fills: Asia 0.204); the risk-floor fixtures (floor 0.498 -> 0.789: stop distances widened, and the gap-through
  test now uses risk 0.9). The eval2 self-test harness hash now covers config/cost_of_record.yaml, so the self-test
  hash changes. Every catalogue gold net result moves down by $0.04-0.13 per round trip, and the 3 x RT risk floor
  rises (2010-17 low-ATR bars see fewer trades). Any catalogue/EVAL-2 explore read before this commit is not
  comparable: it needs a re-run (quant-reviewer). The EVAL-2 deciding cost ($0.230) and RiskGate are unchanged.
