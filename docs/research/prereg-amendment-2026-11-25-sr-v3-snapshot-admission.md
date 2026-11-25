# Pre-registration amendment: minute-revision audit and snapshot admission for `sr-v3`

Amends `docs/research/prereg-2026-11-24-self-retrain.md` (H-100). Written by the data engineer on 2026-11-25
**before the audit below was run**: this file's first commit precedes the first read of any audit number, and the
commit is pushed before the measurement starts. Quant-reviewer signs. Red-team gap 1; execution plan W1-D; ruling D13.

**sr-v2 is not edited.** It runs as signed. Every change here is spec **`sr-v3`** (card section 5: a changed
constant is a new spec version), except item 4, which the quant rules on (bug fix or spec change).

## 1. Why

On 2026-11-20 sr-v2 had 9,289 r2 labels and used 812 of them. The causes are in the admission plumbing, not in the
labels:
- `NOMINAL_FROM` = 2026-11-15 drops every nominal snapshot decided earlier (the W44-W46 bulk);
- the snapshot job reads at most 600 decision bars a night, so the backlog fills slowly;
- the weekly audit read vantage's minutes under `XAUUSD`, while vantage stores them under `XAUUSD+`, so every vantage
  nominal snapshot was "not audited clean" (item 4).

`NOMINAL_FROM` may move only if the minutes a nominal snapshot reads are shown, on the days where it can be measured,
to be the minutes that were known at the decision. That is the audit below.

## 2. The audit (read-only, brain)

**Data.** The three gold accounts (icm-demo, icm-probe, vantage-demo), each under its own store symbol, the broker
feeds only (`bars_1m_live`, `bars_1m_broker`, `bars_1m`; ranks 0-2 of `account_bars.minute_versions`). Minutes with
`ts_utc` in [2026-11-14 00:00, 2026-11-24 00:00) UTC: full days on which both the nominal time and the write time
exist (write stamps began 2026-11-13). Final state = the store at the audit run. aux.db opened `mode=ro`; nothing is
written. No label, R or outcome column is read.

**Two views of the same stored versions.**
- *PIT view*: `minute_versions` as stored (a version counts from its write time when written more than
  `LIVE_WRITE_GRACE` after its close).
- *Nominal view*: the same versions with the write stamps removed, so every version counts from its minute's close.
  This is exactly what a snapshot decided before 2026-11-13 read.

**Statistic R (minute level, per broker, pooled over accounts and days).** Over every minute present in a broker feed
in the final state: the share whose nominal-view value at its close differs from its PIT-view value at close + 2 min
(`LIVE_WRITE_GRACE`), where "differs" is any of the 8 bid/ask OHLC prices off by more than 0.001, or the minute absent
from the PIT view. R at close + 1 h and close + 24 h is reported, not judged.

**Statistic S (snapshot level, per broker).** A sample of gold decision bars decided in [2026-11-17, 2026-11-24)
(the last three-plus days of each frame are stamped), stratified by (account, tf), at most 40 per cell, drawn with
seed 20261125. Each is snapshotted twice with `feature_snapshots.decision_features`: from the PIT view and from the
nominal view. A snapshot differs materially when any of the 39 sr-v2 `FEATURES` differs by more than 0.05 of that
feature's standard deviation over the sample's PIT rows, or is NaN in one view only. S = the share that differ
materially.

**Pass rule, per broker (both must hold):**
1. Wilson 95 % upper bound of R <= **0.5 %**;
2. Wilson 95 % upper bound of S <= **5 %**.

A broker with fewer than 2,000 minutes or 30 sampled snapshots in the window reads **insufficient** (= fail).

These numbers are fixed here and are not revised after the audit is seen. A failure is reported as a failure.

## 3. What sr-v3 admits

- **Broker passed:** a nominal snapshot decided before 2026-11-15 is admitted when, over every minute the snapshot
  read (the ENGINE_MINUTES window), every minute in the nominal view came from the live feed (rank 0): no broker-M1 back-fill (rank 1-2, written
  by the nightly reconcile, often after the decision) and no reference minute (rank 3, never stamped). This check
  needs no write stamp, so it can be run on the old rows. Snapshots decided on or after 2026-11-15 keep sr-v2's
  per-candidate audit (every minute of the last 24 h written).
- **Broker failed or insufficient:** sr-v2's rule unchanged for that broker (`NOMINAL_FROM` 2026-11-15).

The backlog (about 3,500 gold decision bars with no snapshot, most before 2026-11-13) is drained in one bounded night
only if at least one broker passes; otherwise the old bars stay unsnapshotted (their rows could not be admitted).

## 4. Vantage: the audit reads the account's own symbol

`snapshot_audit.audit` was called without `symbol_of`, so it read vantage-demo's minutes under `XAUUSD`. Vantage's
engine and reconcile store them under the traded symbol `XAUUSD+` (brain, 2026-11-25: `bars_1m_live` source vantage
and `bars_1m_broker` source vantage-demo, symbol `XAUUSD+`, all stamped). The audit then saw only the reference
feed's unstamped minutes and excluded every vantage nominal snapshot. The job now passes each account's store symbol.
**Quant to rule:** a bug fix that applies to sr-v2 (the card says the audit reads "every minute visible at its
decision", which means the account's minutes), or an sr-v3 change.

## 5. Other sr-v3 changes in the same spec

- **OOS only for judged pairs (quant Q2).** The OOS read (means, edges, p values, AUC) is computed only for the
  run's judged pairs. Every other arm is fitted and reported by counts only (n rows, n OOS, n kept, n days,
  n control). Eligibility already uses counts only, so the judged set is unchanged.
- **Horizon embargo.** A training row must be decided at least max(1 day, the arm's longest horizon) before the
  test block's first decision (sr-v2: 1 day, too short for multi-day holds).
- **One bar, one row in the gates (quant M2).** Duplicate decision bars across accounts on one feed (icm-demo and
  icm-probe) count once in the MIN_TRAIN / MIN_OOS / MIN_KEPT / MIN_OOS_DAYS gates. Labels on one bar are averaged
  into one row. sr-v2 reports the share per arm as `dup_bar_share` (0 K).
- **Stale reference minutes out (quant H2).** A nominal row that read a reference (rank-3) minute stamped-era old
  (ts in [2026-11-13, decision - 24 h)) is excluded; sr-v2 counts them as `nominal_rank3_beyond_24h`.
- **Labels unchanged.** No label definition changes, so r2 stays the target and no r3 is written; the sr-v2 chain
  is not reset.

sr-v3 is not run as a judged spec until its card is signed (execution plan: 2026-12-06). Until then the weekly job
runs sr-v2 as signed and records, in the run's detail, the usable-row counts sr-v3 would admit per (broker, class,
timeframe), counts only: 0 K (quant Q2/Q4: no OOS, net-R or label outcome is computed for that census).

## 6. Result (filled in after the audit; the rule above is not edited)

Run 2026-11-25 07:54 UTC on the brain (`scripts/research/minute_revision_audit.py` at commit dd7b97e6, after the
threshold commit 6ccb552f was pushed). Read-only; no label or outcome read. 248 snapshot pairs, 0.46 s median each.

| Broker | Minutes | R at close (differ) | R upper 95 % | R 1 h / 24 h | Snapshots | S material | S upper 95 % | Verdict |
|---|---|---|---|---|---|---|---|---|
| icm (icm-demo + icm-probe) | 16,636 | 0.08 % (14, all absent at close) | 0.14 % | 0.04 % / 0 % | 168 | 14.9 % (25) | 21.0 % | **fail** (S) |
| vantage (vantage-demo) | 8,327 | 4.1 % (345: 341 absent, 4 revised) | 4.6 % | 1.7 % / 0.05 % | 80 | 53.8 % (43) | 64.3 % | **fail** (R and S) |

- 152 of 168 icm snapshots and all 80 vantage snapshots differ in at least one feature. The material differences sit
  in the long-window volatility columns (`vol_of_vol_60` 28, `vol_ts_yz_20_60` 26, the `em_yz_*_cost` columns 6
  each). These read minutes from days back, and those minutes include back-fills written after the decision. With
  the write stamps removed, the back-fills are read as if known at their close.
- Vantage's live feed misses about 4 % of minutes at their close. The nightly reconcile fills them later, so a
  snapshot without stamps reads minutes that vantage's engine did not have.

**Decision under section 3:** both brokers fail, so `NOMINAL_FROM` stays at 2026-11-15 for every broker
(`SR_V3.nominal_early_brokers = ()`), and there is no drain night. Rows decided before 2026-11-15 stay out of
sr-v2 and sr-v3. The old backlog is still snapshotted inside the nightly quotas, newest first and after the newer
candidates, so that the record is complete, but no run admits those rows. The rule is not re-run with a looser
threshold. A new audit would need a new pre-registration and a reason other than this result.

**Usable rows under sr-v2 admission** (brain 2026-11-25 on a copy of aux.db taken from a `mode=ro` connection; counts
only, no label value read):

| Cutoff | Broker | Labels | Usable before the fix | Usable after | Of which strategy rows |
|---|---|---|---|---|---|
| 2026-11-20 (the 11-24 run) | icm | 7,821 | 812 | 812 | 161 (RB15 132, RB1H 16, GS1 8, RB4H 5) |
| 2026-11-20 | vantage | 1,468 | 0 | 316 (15m 259, 1h 57) | **0**: every vantage row is a control |
| 2026-11-27 (the 11-29 run) | icm | 8,274 | 1,316 | 1,316 | 274 (RB15 146, RBR15 28, RB1H 20, Z3D 19, ...) |
| 2026-11-27 | vantage | 1,599 | 0 | 454 (15m 370, 1h 84) | **0** |

The fix gives vantage admitted rows, but every one is a control row, so no vantage arm can be fitted. Vantage reads
**insufficient** in every run until a vantage strategy arm records gold candidates.

**Holdout (quant ruling on PR #451, ADR 0100).** The S half of this audit and every snapshot since 2026-11-13 read
minutes from August and September 2026, inside the sealed holdout, as the engine's lookback of post-holdout decisions.
Ruling: **keep** the stored snapshots; the snapshot history is **not** cut; the holdout window is unchanged. A holdout
minute may be read only as lookback input to a feature at a decision on/after 2026-10-01 whose label lies wholly
after it, never as a decision, label, outcome, or in any selection or threshold statistic. Self-retrain models are
forward-only and never scored on the holdout. Any model trained on rows decided before 2027-01-08 that is ever scored
on the holdout excludes 2026-06-23..2026-09-30 from that score. A snapshot decision inside the holdout is refused. The
overlap is one-off and ends about 2027-01-08 (not an owner decision; reported on #34 as information).

What still unlocks rows: the vantage audit-symbol fix (item 4), the per-timeframe quotas (slow timeframes no longer
wait behind the 1m backlog), and time: every new candidate gets a strictly written snapshot once its 90,000 trailing
minutes are all stamped (from about February), and an audited-clean nominal one before then.

## Sign-off

- Data engineer: 2026-11-25 (author; threshold fixed before measurement).
- Quant-reviewer: pending.
