- Account type no longer blocks entries for a week: `nightly_costs` re-runs the classifier every night while the stored
  class is unknown (missing file or "unknown"; `PersistentClassifier.is_known`) and on Fridays only once a class is
  known (evidence thresholds and the two-disagreement rule unchanged). New optional `account_class: raw|standard` per
  account in `config/accounts.yaml` (set for icm-demo only) is the baseline until a measured class is stored;
  `execution/classifier.effective_class` is the one function the engine, smoke and state use; a disagreeing
  measurement makes the stricter class (standard) win and the engine health check warns "declared raw, measured
  standard: check the account type". Entry status: "only N London/NY ticks" is a self-clearing wait ("N of 1,000
  market ticks recorded; checked every night"), a declared class with no measurement is an info note, every other
  unknown reason still "Needs you". RiskGate logic and thresholds unchanged; unknown still blocks. The engine state
  gained `account_class_declared` and `account_class_measured` (golden hashes regenerated).
