- 2026-11-12 (branch fix/pass-budget-log-noise; R134): the engine's "pass budget ... spent after N of N books" line
  no longer warns every minute when every book ran and only 15m+ work was carried (now DEBUG). The WARNING fires once
  per run of passes that left books out; the run's first clean pass logs one INFO "pass budget run ended after K
  passes over S s; L with books left out, most 15m+ parts carried C". Metrics (`passes_deferred`), the carry and the
  zones' deferred flag are unchanged. To see carried-only passes on the brain, raise goldbot.engine to DEBUG.
