# 0106: Aims, objectives and goals of record (owner 2026-11-26)

Status: accepted (owner, 2026-11-26)

## Context
On 2026-11-26 the owner set a new specification for the whole system: aims, model families and their roles,
objectives for data, training and trading, and measurable goals. It is the reference every card, trainer, gate and
report is judged against from today. The text is kept verbatim under "Specification of record". Where it meets an
existing rule, the binding reading is set below under "Decision", made by the program director with the product
owner, CTO, CFO, quant and trading-safety (ADR 0085 roles). The owner's priority rule settles any conflict:
survival first, then consistency, then return.

## Decision
1. **The specification below is of record.** It replaces ADR 0004's north-star as the system's aim. £50+/day on
   opportunity days stays a reported figure, never a target.
2. **Risk goals.** These are the owner's own limits and are stricter than today's, so they need no further sign-off:
   - **Real money:** risk per trade ≤ 1 % of equity, daily loss limit 2 % of equity then stop for the day, max
     drawdown halt at 15 % (the review starts at 15 %, and the 20 % edge is never reached), and order size ≤ about
     1 % of the asset's daily volume. They are enforced in the RiskGate as plain code the models cannot override.
   - **Demo (learning) accounts:** the 11-25 learning caps stay so that the learning volume continues. Every
     promotion read (challenger vs live, paper → real money) is judged on the trades as if sized under the
     real-money limits above, and a model that would breach them is not promoted.
3. **Model families A-G.** These are the roles of record. Every deep model (A, B) must beat the gradient-boosted
   baseline (C) out of sample after costs or it is not deployed. RL (D) does sizing and execution only. LLMs (E)
   feed features only, never place a trade, and are tested only on data after their knowledge cutoff. F supplies
   volatility, regime and sanity checks. G combines A-F by recent out-of-sample performance.
4. **Benchmarks.** Buy-and-hold of the same market over 6+ months, after costs, is added to the random twin. Both
   must be beaten.
5. **Final test period.** The final test period is the sealed holdout 2025-10-01..2026-09-30 (ADR 0085), and it is
   never used for tuning.
6. **Promotion and real money.** A challenger replaces the live model only after beating it for 30-60 days on paper
   or demo, after costs. Real money needs 2-3 months of paper trading that meets the goals below, on top of the
   existing paper → tiny-live gate and the owner's unlock phrase. Live unlock and funding stay owner-only.
7. **Retraining.** Monthly at the least, on a fixed schedule, walk-forward. The weekly and daily refits continue.
   Every change is reversible, and any past model can be rebuilt exactly from its logged data version, settings,
   seed and code sha.
8. **Gaps** against this specification are tracked in docs/data/sufficiency.md (data) and the findings register
   (everything else), each with an owner and a date. The program director keeps the gap map current. For B, the
   order book models, the broker serves no depth data today, so the data team owns that route.

## Consequences
- Cards, the history trainer and the training report state which aim, objective and goal each item serves. The
  goals table is the acceptance list for promotion and for real money.
- The kill/continue test of 2027-01-15 (G3) is unchanged.
- Nothing loosens: every new limit is at least as strict as the one it meets.

## Specification of record (owner text, verbatim)

```
AI TRADING SYSTEM: AIMS, OBJECTIVES AND GOALS
(specification for training deep learning and other model types)

PRIORITY RULE
When goals conflict: survival first, then consistency, then return.

=== AIMS ===
1. Grow capital at the best risk-adjusted rate after all costs.
2. Protect capital first, so no loss can end the project.
3. Trade four horizons from one platform: scalping, intraday, swing,
   long-term.
4. Work at any account size without redesign.
5. Adapt to changing markets and improve without human intervention.
6. Curate its own clean, point-in-time historical dataset.
7. Train models only on what was knowable at the time, so backtest
   results carry over to live trading.

=== MODEL FAMILIES AND THEIR ROLES ===
A. Deep learning, sequence models (transformers, temporal convolution,
   LSTM, time-series foundation models)
   Role: forecast return, direction and volatility from price and volume.
   Horizons: intraday, swing.
B. Deep learning, order book models (CNN or compact transformers on
   limit order book data)
   Role: short-term price and liquidity prediction.
   Horizons: scalping, intraday. Must meet latency budget.
C. Gradient-boosted trees (XGBoost, LightGBM, CatBoost)
   Role: baseline on tabular features; every deep model must beat it.
   Horizons: intraday, swing, long-term.
D. Reinforcement learning (PPO, SAC, TD3)
   Role: position sizing and order execution, trained in a simulator
   with realistic costs. Not the primary signal source.
E. LLMs and multimodal models
   Role: turn news, filings, earnings calls and chart images into
   scores and features. Feeds other models; never places trades alone.
   Horizons: swing, long-term.
F. Classical statistical models (GARCH, regime-switching, cointegration)
   Role: volatility estimates, regime detection, sanity checks.
G. Ensemble / meta-model
   Role: combine A to F, weighted by recent out-of-sample performance.

=== OBJECTIVES ===
Data curation
- Collect price, volume, order book, news and fundamentals automatically.
- Clean bad ticks, gaps, duplicates; adjust for splits and dividends.
- Store when each data point became public, not only what it describes.
- Keep delisted assets to avoid survivorship bias.
- Generate labels per horizon using only data after the decision point.
- Version every dataset; block training if quality checks fail.

Training
- Split by time, never randomly, with a gap between train, validation
  and test periods.
- Retrain on rolling windows (walk-forward).
- Optimise for profit after fees and slippage, not raw accuracy.
- Normalise features using past data only.
- Regularise deep models (dropout, early stopping, weight decay) and
  train several seeds per model.
- Test LLM components only on data after their knowledge cutoff.
- Log every run: data version, settings, seed, results.

Trading and control
- Run one strategy module per horizon; allocate capital by live
  performance and correlation.
- Size positions as a percentage of equity, scaled by volatility.
- Enforce risk limits in plain code the models cannot override.
- Execute at lowest achievable cost; record expected vs actual fills.
- Promote a new model only if it beats the live one after costs.
- Run unattended with alerts, safe shutdown and a manual kill switch.
- Log every decision for audit and tax.

=== GOALS (MEASURABLE TARGETS) ===
Performance
- Beats buy-and-hold of the same market over 6+ months, after costs.
- Live Sharpe ratio above 1.0.
- Trading costs under one third of gross profit.

Risk
- Max drawdown under 15-20% of account.
- Daily loss limit 2% of equity, then stop for the day.
- Risk per trade 1% of equity or less.
- Order size at most about 1% of an asset's daily volume.

Data
- History covers a rising, falling and sideways market per asset.
- Under 0.1% missing bars after cleaning.
- Prices cross-checked between two sources.
- Zero look-ahead leaks, verified by automated test per dataset version.

Model quality
- Every deep model beats the gradient-boosted baseline out of sample,
  or it is not deployed.
- Results stable across at least 5 random seeds.
- Strategy stays profitable when parameters are shifted slightly.
- Final test period never used for tuning.
- Live performance within about 30-50% of backtest; larger gap
  triggers review.
- Scalping models respond within the latency budget set for the venue.
- Any past model can be rebuilt exactly from logs.

Operations
- Retraining on a fixed schedule (e.g. monthly), every change reversible.
- Challenger beats live model for 30-60 days in paper trading before
  promotion.
- 2-3 months of paper trading meeting these targets before real money.
- Safe shutdown on any data or broker failure.
```
