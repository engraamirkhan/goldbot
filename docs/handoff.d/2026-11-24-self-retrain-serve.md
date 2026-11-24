- **Self-retrain serving hook (owner 2026-11-24).** The engine now applies the weekly retrain's ACTIVE filters
  (`goldbot/engine/meta_filter.py`).
  - **Where.** `exploration._send`, after the entry refusals, before any budget, slot or cap and the RiskGate.
    Drop-only: it never adds an order, changes a size or touches an exit.
  - **Which arms.** Demo strategy arms only, gold 15m/1h/4h first units, on an account `is_demo` says is demo. Never
    controls, replay-only, cost, learning (GD1D/GD1P) or owner-experiment arms, pyramid adds, 1m/5m or 1d/1w.
  - **Record.** A dropped candidate stays in `exploration_obs` (taken 0, blocked `self_retrain:<model_version>`) and is
    replay-labelled; every scored candidate carries `meta_p` and `model_version`.
  - **Failure.** Any error fails open (sent as before), logged; health `engine:<account>` WARNs for 15 min.
  - **Switch.** `research.self_retrain.enabled` and `.serve` (`EngineConfig.meta_filter_serve`, off by default).
  - **Today.** No model is ACTIVE yet (earliest the 2026-12-13 run), so nothing is dropped until then.
