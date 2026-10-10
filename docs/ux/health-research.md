# Health and Research screens

These are two read-only dashboard tabs for the owner, on a phone or a Mac. The design calls for them under
Explainability ("PSI heatmap, reliability curve, CUSUM trace, allocator weights") and under Drift and health. They
follow the style of `approval-card.md`: the same tokens, red only for things that stop trading, and state always shown
in words as well as colour.

## Data

| Endpoint | Role | Built from (goldbot/api/explain.py) |
|---|---|---|
| `GET /api/health` → `HealthView` | any logged-in user | `state/drift.json`, `state/shadow_book.json`, `state/agents.json`, plus the deploy, data-quality and drift checks from `ops/health.py`, evaluated on request |
| `GET /api/research` → `ResearchView` | any logged-in user | `state/research_registry.jsonl`, `state/research_plan.json`, `docs/research/hypotheses.md` (pipe tables parsed) |

Both endpoints only read files and never write. An unreadable `drift.json` is shown as a halt banner, because the
engines fail closed on it. A corrupt plan shows its error and the rest of the screen still renders.

## Health

On a phone the screen is one column. From 960 px the charts sit in two columns.

1. **System-halt banner** (`role=alert`, red). Shown only when `system_halt` is set or `drift.json` is unreadable.
   It gives the reasons and the time, and says that exits are unaffected. It shows two commands, each with a Copy
   button: `python -m goldbot.ops.run drift-review` to look at the report, then
   `python -m goldbot.ops.run drift-review --clear "what you checked"` to lift the halt.
2. **Health checks**: chips for Deploy, Data quality and Drift watch, each with ok, warning or FAILING and the
   check's one-line reason.
3. **Agents**: one row per champion with its state (Trading, or Halted since …), size (100%, or 50%: sized down),
   ECE, capital share and every finding in plain words. Below 720 px each row becomes a labelled card.
4. **Feature drift (PSI)**: a heatmap table of up to 15 features (worst PSI first) by agent. Cells are banded below
   0.1 (stable), 0.1–0.25 (drifting, warns) and above 0.25 (sizes the agent down). Every cell shows its value, a
   hidden band word for screen readers and a hover title. A dot marks a feature the agent does not use.
5. **Calibration**: a reliability curve over each champion's trailing 100 closed, taken shadow trades, with 10 p bins.
   The dashed diagonal is perfect calibration and the trade count sits beside each dot. A picker switches between
   "All agents" and each agent. The caption gives ECE (sizes down above 0.08) and the Brier score.
6. **CUSUM on trade residuals**: one small chart per agent, plotting the downward CUSUM statistic after each closed
   trade against the dashed halt line at h. When the alarm has fired the line turns red and the label reads
   "alarm: halted".
7. **30-day drawdown vs 1.5× backtest**: one bar per agent with a marker at the limit. The bar is amber above 75% of
   the limit and red above it, with the label "system halt". If no backtest drawdown was recorded, the screen says
   so instead of drawing a marker.

Every chart has a native hover title on its marks, an SVG `<title>` that a screen reader announces, and a
"Show as table" alternative. Times are in local time, with UTC in the hover title.

## Research

1. **Trial budget**: a meter showing "N of 20 used · M left" for the current quarter. It is amber when 20% or less
   of the budget is left and red when the budget is spent. A one-line note explains why the budget is fixed.
2. **Research director's plan**: when the plan was made, the trials used then, the holdout window, and the focus
   families with their budget, evidence (out of 3) and reasons. A plan older than 21 days carries a stale warning.
   The evidence per family table is collapsed by default.
3. **Trial registry**: the newest 200 trials, with #, date, family, timeframe, status, gross R (t) and net R (t) of
   the rule over every candidate, the event count, and gates. The gates cell reads "passed", or "failed: dsr, …"
   with every check in its hover title. The rationale is truncated, with the full text on hover.
4. **Hypothesis portfolio**: each table in `hypotheses.md` is a collapsible section. The first section is open. Each
   row is a card so the long prose stays readable at 360 px.

## Empty, loading and error states

| State | Copy |
|---|---|
| Loading | "Loading health…" / "Loading research…" (`role=status`) |
| API error | "Could not load health (…). Safety state is still on Overview and Approvals." (`role=alert`) |
| No drift check | "No drift check yet. The drift watch runs daily on the VPS once a champion model is live." |
| No PSI | "No PSI yet. It needs a live champion with at least 50 recent candidates and a training reference." |
| No shadow trades | "No closed shadow trades yet. The curve fills in as the shadow book records outcomes." |
| No plan | "No plan yet. The research director writes one every Saturday." |
| No trials | "No trials recorded yet. The registry fills as research trials run." |
