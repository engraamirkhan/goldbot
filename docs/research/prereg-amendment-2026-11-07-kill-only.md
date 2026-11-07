# Pre-registration amendment 2026-11-07 (kill-only): gold 02:00-07:00 UTC entry block, Vantage probe window, sequential early kill (mSPRT), cost-neutral control comparisons, tier-1 event-window leak check, F-1 pit window

Status: **SIGNED, quant-reviewer, 2026-11-07 (Sat) 09:00 UTC**, before any shadow-log row, mSPRT statistic, event-window tag or pit-window fill exists. Applies from the deploy of the rank 1+3+7 config PR (target Sun 2026-11-08 before 22:00 UTC; fallback Wed 2026-11-11). Source: the directors' plan of 2026-11-07 (`~/tmp/director_plan_1107.json`, ranks 1, 3, 4, 7 and the D6 check). Amends: `prereg-2026-10-25-high-volume.md` (H-61 reads), `prereg-2026-10-27-scalp-volume.md` section 4, `prereg-2026-10-26-time-structure.md` 2.2, `prereg-amendment-2026-10-30-kill-rules.md` A1, `arms-2026-10-25.md` 8.0, and the learning-arm blocks of `config/exploration_arms.yaml`.

## 0. What this amendment is, and is not

- **Kill-only.** Every rule below adds a way for an entry to be refused, a probe not to be sent, or an arm to be retired earlier. None adds a way for an arm to continue, graduate, scale, or be promoted. The only bias any of them can introduce is a false kill. A "revert" (section 3.3) restores the status quo ante for a block; it is not a promotion of anything.
- **No reading rule changes.** Every existing read (H-61 at the 300th admissible fill on >= 20 session days; H-81 section 4; W1/W2 family reads; A1 matched-control futility; A2 readability; A5 history-bounded entries; O-11/O-12) keeps its sample, statistic, bar and date. No promotion threshold changes: promotion still needs a CONFIRM on 2018-01..2025-09 under the frozen rule, the DESIGN gates (1,500 candidates, 60 per fold, 3 positive years incl. 2021-22, DSR >= 0.95 on 200+ trades at the registry's real K), and demo fills are never promotion evidence.
- **No K.** Nothing here is a trial: no registry row, no budget slot, no change to `n_trials_effective`. Every arm id, hash and existing K row is unchanged. Anything listed under "needs a new card" (section 9) is counted in K the day it is proposed.
- **Holdout** 2025-10-01..2026-09-30 is not read by anything here.
- **Interpretation.** Each read below is read once, by the rule as written here, on the first n rows by decision time, with no interim read of the decision statistic. A number that is "reported" is never a trigger.

## 1. Disclosures (integrity)

1. The 02:00-07:00 UTC window (rule 1) was chosen **after** looking at the 10-day demo data: gold entries 02-07 UTC -£394.7 on 634 fills (6 of 8 days negative), the worst of about 24 hourly and 6 session cells. The expected "avoided loss" is therefore overstated by selection (per-fill SD about £2.8 at 0.01 lot, SE of the window sum about £70, max-of-24 selection worth roughly £100-150 of the -£395; the cost component, about £0.28 x 634 = £178, is mechanical). The block only stops trades; its counterfactual is the shadow log; its only reopening path is section 3.3. Independent support: H-83 when-map, 0 of 24 Asia cells net positive on history, read before the demo data existed.
2. The side split inside the window (longs -£313.9 n 302, shorts -£80.8 n 332) and the hours 13/14/16/18 UTC figures (-£50.5 / -£50.3 / -£83.7 / -£53.0) are **known**. No side-specific or hour-specific variant is created by this amendment; each would be a new card counted in K (section 9).
3. The NFP window of 2026-11-06 (108 entries, -£85.5) was seen before the event-window check (section 7) was written. Those fills are **excluded** from its 150-fill read, which starts at the deploy.
4. The learning-forward baseline for the rank 1 secondary trigger is the 11-04..11-06 run rate (icm-demo about -£13.5/day, vantage about -£28.7/day, icm-probe about -£0.2/day; daily SDs £64.6 / £53.4 / £56.6). Three days cannot distinguish any account from zero; the trigger is a re-review, never an action (section 3.4).
5. The matched-excess figures that motivated the sequential kill (D8 +0.003 R over 113 cells; chair -0.015 R over 86 gold cells; D10 +0.032 R, SE 0.040) were read before the boundaries below were fixed. The boundaries do not depend on them: sigma, delta, alpha and beta are set from the 2026-10-30 re-derivation and the design, not from any arm's result.

## 2. Definitions (bind every section)

- **Gold.** Instrument key `gold` (`exploration_data.instrument_key`): XAUUSD on ICM, XAUUSD+ / XAUUSD247 on Vantage. XAU crosses (XAUEUR, XAUAUD, XAUJPY) are **not** gold here: the replay labels gold only (`label_candidates`), so a refused cross signal would have no counterfactual. Their in-window total (-£12.5 across all hours) is reported, not blocked.
- **Decision time.** The signal bar's close (`exploration_obs.decision_ns` = `Pending.bar_ts`), in UTC. A window is tested on the decision time, inclusive of its start and exclusive of its end: 02:00:00 is inside, 07:00:00 is outside.
- **Exploration arms in scope of rule 1.** Gold arms with role `edge`, `learning`, `owner_experiment` and every random or dedicated control (`role: control`) that serves them. **Exempt:** `role: cost` arms (F1A, G1A) and their dedicated controls (F1C, G1C), whose fills are registry inputs for O-11/O-12 and whose rule windows (G1: 17:00 New York-08:00 London) lie in the blocked hours; cost probes (mirror and scheduled), which section 4 governs.
- **Session.** `DEFAULT_SESSIONS.session_label` (`goldbot/data/calendar.py`): asia / london / newyork, as the daily review uses.
- **Cell.** (session_day, session, side), session_day per `exploration_data.session_day`. An arm's matched control is the control row its pre-registration names (a learning arm's `learning.control`; the stratum's ZCD/ZCV; S0/S0R/I0/W0 for the rest), per broker, never pooled across brokers.
- **Gross R.** Mid-price R of the fill or replay: `label_mid_r` for a replayed candidate; for a realised fill, net R plus the fill's own measured cost per R (below).
- **Own measured cost of a fill** (account currency): the spread at send (`spread_usd` x oz) + realised slippage on each leg (fill minus requested, exit side likewise, floored at 0 per leg) + broker `commission` + paid `swap`, all from the fill's own `exploration_trades` row; cost per R = cost / `stop_loss`.
- **Net R.** Realised `pnl` / `stop_loss` for a fill; `label_sim_r` for a replayed candidate (the broker's cost table in force at the decision: spread via the bid/ask path once, slippage per leg at max(measured, 0.15 USD/oz), commission, swap per rollover night paid only).
- **Matched excess (decision statistic, section 6).** Over cells with at least one arm and one control observation: the fill-weighted mean over cells of (arm mean gross R - control mean gross R). Cost-neutral by construction. Reported beside it, never a trigger: the net-vs-net excess with each side at its own cost.
- **Own-net hurdle.** The arm's mean net R at its own measured cost, > 0, and gross >= 2 x cost per R (Rule 2 of the standing rules). Required for any non-kill outcome of any read, as before.
- **K at a read.** `goldbot.research.registry.n_trials_effective` of the brain's `state/research_registry.jsonl` on the read day, written into the read (local snapshot 2026-11-07: 162 trials, 86 in 2026Q4; the brain's count is higher and is the one used).

## 3. Rule 1: gold 02:00-07:00 UTC entry block, with a shadow log

### 3.1 The block
- From the deploy, every in-scope gold exploration signal (section 2) with decision time in [02:00, 07:00) UTC is **refused** on icm-demo, icm-probe and vantage-demo. Exits, stops, targets, holds and the RiskGate are untouched; a position opened before 02:00 keeps every exit.
- The block is an **engine-level refusal** with its own journalled reason (`exploration_refused:window_0207`), applied after the arm's rule has produced the signal and before any room, slot or cap check. It is **not** an edit of any arm's `window_utc`/`window_local`: `arm_hash` covers `params`, so an edit would be a new arm id and a new K row, and a narrowed rule would produce no signal to shadow.
- Scope is 02:00-07:00 UTC only. Hours 13/14/16/18 UTC and any side-specific window are not blocked (section 9).

### 3.2 The shadow log (the counterfactual)
- Each refused signal is recorded through the existing untaken-candidate path (`exploration.candidate(..., blocked="window_0207")` -> `exploration_obs` with `taken = 0`, `blocked_reason = "window_0207"`, side, `stop_dist`, `target_dist`, `max_hold_s`, `decision_ns`, `is_control`, broker). The decisions journal row carries the tick's bid and ask at the refusal (informational).
- Scoring is the existing replay (`exploration_data.label_candidates` -> `replay_label`): entry at the open of the first 1m bar at or after decision + the arm's `send_delay_s`, on the broker's own 1m bid/ask bars, the arm's own stop and target, stop first within a minute, hold as the arm's; costs as section 2 "Net R", from the broker's cost table **effective at or before the labelling cutoff** (the existing check raises otherwise). The Asia slippage cell charged is the one in force at the decision; section 4 freezes the Vantage Asia cell, so the read states the cell version used and reports sensitivity at the broker's all-session cell.
- Controls in scope are refused and shadowed by the same path, so the matched comparison in 3.3 is shadow-vs-shadow, one code path for both sides.
- A shadowed refusal whose replay does not resolve by the cutoff stays NULL; it is never censored to zero.

### 3.3 Revert rule (the only way the window reopens)
- **Sample.** The first **300** shadowed refusals of non-control in-scope arms, by decision time, pooled over the three accounts, each resolved by the read's cutoff; the matched control sample is every shadowed control refusal in the same cells.
- **One read**, at the 300th resolved row, no interim read of the statistic. Expected date about 2026-11-20 (reported in the Fri 11-20 note).
- **Revert** (remove the block for every in-scope arm on every account at once) only if **both**: (a) the 300 refusals' mean net R at measured cost > 0 **and** their total in account currency > £0 (sized at each arm's own lot and stop); and (b) their matched excess (section 2, gross, by day and side) > 0. Otherwise the block stays and is next readable only under a new card (section 9).
- A revert on one account, one side, one arm family or one sub-window is a variant and is forbidden.
- Reported with the read, never a trigger: net-vs-net excess at own cost, the side split, the per-account split, the per-hour split, the figure at the all-session cost cell.

### 3.4 Secondary re-review trigger (re-review only; can never reopen the window)
- **Sample.** The first 300 gold fills of in-scope non-control arms with decision time in [07:00, 21:00) UTC from the deploy, excluding fills tagged under section 7 (tier-1 -30/+60 min), which are reported separately.
- Trigger if **both**: matched excess (section 2) <= +0.052 R **and** each account's mean net £/day since the deploy is not above its 11-04..11-06 run rate.
- Consequence: the performance-analyst writes a re-review for the research-director; no automatic change. Any proposed change is a new card.
- The raw -£0.62/fill figure is reported, never a trigger.

## 4. Rule 3: Vantage probe window and S0-XAUJPY (measurement only; no K)
- vantage-demo scheduled cost probes send only with a slot time in [07:00, 21:00) UTC, at `every_min: 60`; 0-1 Asia probes a day is the acceptance figure. Mirror probes on icm-probe follow the same hours. Probes are cost measurement under `prereg-cost-measurement-2026-10.md`; they are not research, carry no K and are never read for edge.
- The Vantage Asia slippage cell (202 fills) stops accruing: it is frozen at its last fit, the cost of record records the freeze, and the rule 1 read charges that frozen cell (3.2). Restore Asia probes only if a pre-registered card needs an Asia cost cell with fewer than 50 fills.
- `S0-XAUJPY` (a free-standing per-symbol control; LB-XAUJPY's dedicated control is LBC-XAUJPY) is retired through the retire path; its K row, arm id and hash stay.
- ZCV and every other Vantage short-hold gold arm stay on under their existing matched-control kill rules (H-81 section 4, A1) and section 5. Nothing is switched off wholesale.

## 5. Rule 4: daily sequential early kill (mSPRT), kill-only
- **Scope.** Every gold and non-gold exploration arm with role `edge`, `learning` or `owner_experiment` that has a matched control (section 2). Queue order (London arms first) is operational and changes no statistic.
- **Observation.** One cell c = (session_day, session, side) with n_a >= 1 arm fills and n_c >= 1 control fills, closed by the read: x_c = arm mean gross R - control mean gross R (section 2), variance v_c = sigma^2 (1/n_a + 1/n_c), **sigma = 1.12 R fixed** (per-fill SD re-derived 2026-10-30 from `exploration_trades`, 0.97-1.16 by side); never estimated from the arm under test.
- **Hypotheses.** H0: excess <= 0 vs H1: excess >= +0.15 R (delta = 0.15 R, the smallest effect a demo read can find, A1).
- **Statistic.** Log likelihood ratio over cells, l_n = sum_c (delta x_c - delta^2 / 2) / v_c.
- **Boundaries.** Futility (kill): l_n <= B = ln(beta / (1 - alpha_0)) with beta = 0.20, alpha_0 = 0.05 (B = -1.558, **undeflated**). Upper: l_n >= A = ln((1 - beta) / alpha_K) with alpha_K = alpha_0 / K, K = `n_trials_effective` on the read day (section 2); the K used is written into the read.
- **Floor before a kill can fire:** at least 20 arm fills over at least 5 session days in cells with a control. No floor on the upper boundary.
- **Daily evaluation** at 18:00 UTC on fills closed by then (validity of the SPRT does not depend on the check cadence). Crossing B retires the arm that day through the retire path (rule recorded as "mSPRT futility, prereg-amendment-2026-11-07"), its dedicated control with it; a pooled read unit retires only when every arm in it crosses. Retired ids keep K.
- **Crossing A changes nothing about promotion.** It only exempts the arm from this futility kill until its existing fixed read (H-61 / H-81 / W2 / A1), which is still read as written; the arm's demo fills remain non-promotion evidence; `live-candidates.md` lists nothing on it without a CONFIRM.
- **Expected stopping (calibration, not a rule):** about 170 arm fills at true excess 0, about 75 at -0.10 R, more than 300 near +0.05 R (then the fixed read governs). The plan's "60-120 fills" is the negative-excess case only.
- **Self-review 2026-11-20 (plan rank 4 kill rule):** if no arm has been retired earlier than its fixed read would have retired it, the daily mSPRT is dropped and the A1/A2 reads stand alone. No K.

## 6. Rule 5: cost-neutral comparison for every "beats control" test
- From this date, every control comparison in any read (A1 matched excess, H-81 section 4 "family minus control", the rank 5 cost-floor card, section 7 here, section 8 here, the learning-arm dated reads, mSPRT) reads the **gross (mid) matched excess** of section 2 as its decision statistic and applies the **own-net hurdle** separately. Reason: controls carry more cost per R than arms (non-gold: spread £0.121 vs £0.065, slippage £0.034 vs -£0.001, median stop $2.23 vs $3.00; gold about equal), so a net-vs-net excess, whatever each side is charged, is biased in the arm's favour by about the cost gap, roughly +0.03-0.05 R on the non-gold pool. The cost-neutral statistic is never larger than the net-vs-net statistic where controls cost more, so every kill is at least as likely as before.
- Where a stop-distance-matched control exists (same stop form and distance), it is the preferred control; otherwise the pre-registered control with the cost-neutral statistic.
- The net-vs-net excess with each side at its own measured cost is **reported** next to it in every read.
- No arm is promoted on any matched-excess read; promotion needs the CONFIRM and gates of section 0.

## 7. Rule 1a: tier-1 event-window leak check (pre-registered check; a possible refusal; not a trade, not a line)
- **Tag.** Every gold exploration fill (in-scope arms and their controls; not probes, not cost arms) whose decision time lies in [event - 30 min, event + 60 min] of a tier-1 release (settings `blackout.events`: CPI, NFP, FOMC, PCE) that was in status `scheduled` with `available_utc` <= the decision time, using the point-in-time logic of `arm_rules.blackout_windows` with -30/+60 (not the live `blackout_window`, which reads the frame as loaded). The tag is written at labelling time from the calendar store and is reproducible.
- **Sample.** The first **150** tagged **non-control** fills by decision time from the deploy. The 2026-11-06 NFP fills are excluded (disclosure 1.3). One read at the 150th, no interim read; CPI 2026-11-10 fills are the first in.
- **Ship rule 1a** only if **both**: matched excess (section 2, tagged arm fills vs tagged control fills by day and side) < 0 **and** mean realised net < -£0.62 per tagged fill.
- **Rule 1a** if shipped: entries of in-scope gold arms refused in [event - 5 min, event + 60 min] of the same events (entries only; exits untouched; demo exploration path, not the RiskGate), journalled `exploration_refused:tier1_window` and shadowed exactly as 3.2. Its own revert mirrors 3.3 on the refusal's own window: first 300 shadowed refusals, net > 0 and matched excess > 0.
- Nothing here sizes, scales or trades a release.

## 8. Rule 7: F-1 pit window (signed with conditions; a cost arm, never an edge read)
- F1A/F1C entries are refused outside 08:00-13:30 America/New_York (13:00-18:30 UTC under US winter time), as an engine-level refusal with the control on the same tz-aware window; arm ids and hashes unchanged; refused signals recorded as blocked candidates.
- **Conditions (from `prereg-2026-10-26-time-structure.md` 2.2, 2.3, unchanged by this amendment):** F1A's demo fills are not strategy evidence and no F1A - F1C comparison on them can continue, scale or promote anything; the only outcome the 100-pit-fill read can produce is **off** (net at own cost <= the matched control by the section 6 statistic, or the 95% upper bound of signal-time cost per R above half the history gross). Expected weekly gain is 0 by rule, not "0 to +0.3 %". The `ny_pit` holdout ban stands.
- **Cost read protection.** Because the pit restriction narrows the signal-time cost sample toward New York-session costs, O-11's cost condition is charged at the **worse** of the pit-window measured cost and the broker's all-session cost cell; the restriction cannot lower the cost O-11 pays.
- Hard close 2026-12-18 if fewer than 100 pit fills; no extension or widening.

## 9. Forbidden without a new card counted in K
Any of: a block on hours 13/14/16/18 UTC or any other window; a side-specific Asia variant; reopening 02-07 UTC for any arm, side or account outside 3.3; a per-account, per-family or sub-window revert; an Asia cost cell restart outside section 4; a sizing or news-direction rule from section 7; any change to sigma, delta, alpha, beta or the floors of section 5; any F1A edge read on demo. A new card must show gross excess > 0 against its matched control (section 6), cost per R < 0.06 and gross >= 2 x cost, on history first.

## 10. Implementation acceptance (the config PR; failing tests first, read-only check by quant and trading-safety)
1. Boundary test by decision time: 01:59:59 and 07:00:00 sent, 02:00:00 and 06:59:59 refused with `window_0207`; exits of a position opened at 01:45 run unchanged through 02:00-07:00.
2. A refused signal appears in `exploration_obs` with `taken = 0`, `blocked_reason = "window_0207"`, its side, stop, target, hold and decision time; `arm_hash` of every arm unchanged (`load_arms(check_hash=True)` passes on the deployed file).
3. Truncation test: labelling a refused candidate with a cutoff before its exit leaves `label_sim_r` NULL; a cost table `effective_utc` after the cutoff raises (existing check, kept under test).
4. Cost arms F1A/F1C/G1A/G1C are not refused by `window_0207` (G1A signal at 03:00 UTC sent).
5. Probes: no scheduled or mirror probe sent with a slot outside [07:00, 21:00) UTC on vantage-demo and icm-probe; one per 60 min inside it.
6. Event tag: a calendar row with `available_utc` after the decision does not tag; a `scheduled` row 20 minutes before the decision does; -30/+60 boundaries inclusive.
7. mSPRT script (research module, by Fri 11-13): reproduces l_n on a fixture with hand-computed cells; sigma, delta, A, B and the K used are in its output; the kill writes the rule string above.
8. F1A/F1C: 12:59 NY refused, 08:00 NY sent, 13:30 NY refused; control shares the window.

## 11. Reads and dates
- Mon 2026-11-09 08:00 UTC: refusals logged with the fields of 10.2; Vantage Asia probes 0-1.
- Daily 18:00 UTC from 11-09: section 5 evaluation; section 6 statistics in every table.
- At 150 tagged fills (section 7) and at 300 shadowed refusals (3.3): one read each, dated in the Fri note of the week they land.
- Fri 2026-11-20: section 5 self-review; 3.4 trigger check once 300 out-of-window fills exist.
- 2026-12-18: F-1 hard close check.

## 12. Sign-off
Signed: quant-reviewer, 2026-11-07. The text is kill-only; every read above is fixed in sample, statistic and date before any of its data exists; K, the quarterly budget, every reading rule, every promotion threshold and the holdout are unchanged. Co-signature by the evaluator is requested on sections 5 and 6 before their first daily read (Mon 2026-11-09 18:00 UTC); rules 1, 3, 7 and the section 7 tag take effect at the deploy.
