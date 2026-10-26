# 0043: Exploration arms: budget, hosts and demo rotation

Status: decided (CFO, 2026-10-25) under the owner's delegation of demo limit decisions to the expert panel
(2026-10-18, as ADR 0042 part 1). **Tighten-only:** no RiskGate limit, ceiling, `daily_loss_limit_abs`,
`budget_share` or drawdown stage changes. The one act that lowers a high-water mark (the rotation, section 2) stays an
owner-authenticated act (TOTP), as every mark lowering is today. Implementation needs trading-safety review (list at
the end). Builds on ADR 0042 (icm-probe £220 day, probe budget £198, weekly 6%, capital flows D1-D4), ADR 0037
amendments 4-5 (probe-only host, scheduled probes) and the research program `docs/research/high-volume-learning-2026-10-25.md`
(branch `research/high-volume-learning`; H-60/H-61). ADR 0004's £50 real-money daily limit is untouched; nothing
here applies to icm-demo or any live account.

## Context

**Facts.**
- Research program (research director, 2026-10-25): about 24 exploration arms, about 290 trades a day, designed for
  three probe-only hosts (one per style, each with its own £198).
- Program director, 2026-10-25: **no new demo accounts** (owner: use the two existing ones). All arms run on
  `icm-probe`. `icm-demo` is untouched.
- `icm-probe` today (`config/accounts.yaml` at `2ce9ccb2`): about £11,000 demo, `daily_loss_limit_abs` 220,
  `cost_probe.budget_share` 0.9 (£198), ceilings weekly 0.06 (£660), `max_open` 4, `max_loss` 45, `probe_only`.
- The capacity is one host's £198 a day, a third of what the program assumed.

**Risk mechanics that drive this decision** (read-only, `2ce9ccb2`):

| # | Finding | Consequence |
|---|---|---|
| K1 | `RiskGate.update_stage`: drawdown from the mark of 8% or more gives SIZE_DOWN, which halves the minimum-lot exception cap to 0.5% (£55). 12% gives HALTED | at -8% every stop above £55 is refused; at -12% the account stops |
| K2 | `Engine` re-arm (`runner.py` re-arm handler): a HALTED account re-arms only after 10 trading days of positive paper shadow, then 30 days in propose mode. Probes need auto mode (ADR 0037) | a 12% halt on icm-probe costs **at least 40 days with no learning** |
| K3 | The owner's re-arm acts only on a HALTED engine, and it is **global**. It is one bus `rearm_id`, and every engine whose dynamic limits reverted also runs `limits.reset` | the re-arm cannot rotate icm-probe at -8%. Using it would also touch icm-demo |
| K4 | ADR 0042 D3: on a deposit the mark becomes `max(mark + flow, balance)` ("a loss before a deposit is not forgiven") | **a top-up alone does not clear the drawdown.** From -8% (£10,120 against an £11,000 mark), an £880 top-up leaves -7.4% against £11,880 |
| K5 | 0.01 lot = 1 oz, about £3,120 notional. The 6x-equity notional rail and margin at 1:20 (level ≥ 300%) allow about 19-21 positions on £11,000 | the sum of the arms' designed `max_open` (about 33) does not fit |

**Expected drift** (research program arithmetic; **n = 0 measured, no profit implied**). The rows assume zero gross
edge.

| Case | Cost basis | £/day | %/week | Trading days to -8% (£880) |
|---|---|---|---|---|
| Base | measured spread $0.09/oz plus commission | -£38 (scalp -£30, intraday -£8) | -1.7% | about 23 |
| Pessimistic | ADR 0004 prior, $0.32/oz round trip (about £0.24 per 0.01 lot x about 290 trades) | about -£70 | -3.2% | about 13 |

Per-arm stops (H-61: scalp -30 R within 300 trades) end most cost-bound 1m arms in about 3 weeks. That costs about
£500-600 in total, and the drift then falls towards the intraday rate. Expect the first rotation after **3-5 weeks**
and later ones after 8-12 weeks.

## Decision

### 1. The £198 probe budget: sleeves, per-arm caps, slots

Every sleeve is measured as the cost-probe budget is today (ADR 0037 am. 2): **today's realised loss + open risk at
the stops + the new trade's loss at its stop ≤ sleeve £**. A profit adds nothing. These caps are a tighten-only
layer before the RiskGate (research E3). The RiskGate, the £220 and the £198 bind as they do now.

| Sleeve | £/day | Share | Slots (open) | Per-trade cap | Why |
|---|---|---|---|---|---|
| P0 scheduled M1 cost probes | 15 | 8% | 1 | stop ≤ $3/oz (ADR 0037 am. 5, about £2.25) | the cost truth every arm is judged against. About 75 probes at £0.12-0.24 each |
| M mirror probes (H-59, 13 agents at their own stop) | 45 | 23% | 3 | `max_loss` £45 (ADR 0042, unchanged) | promotion evidence for the roster. One native 4h mirror, or two or three 1h mirrors |
| Scalp arms S0-S6 | 50 | 25% | 8 | arm table, at most £3 | about 170 labels a day. Expected drift £30, SD about £25: the sleeve binds on about 20% of days |
| Intraday arms I0-I3 | 48 | 24% | 5 | arm table, at most £17 | lowest demo £ per label among the strategy arms; holds the H-50 gross-edge lead |
| Swing arms W0-W3 | 40 | 20% | 1 | **£40 (0.36% of £11,000)** | see below |
| **Total** | **198** | 100% | **18** | | ≤ £198 = 0.9 x £220. 18 ≤ about 19 by K5, with 1 slot of headroom |

**Swing per-trade cap: £40, one swing position at a time.**

| Option | Effect | Verdict |
|---|---|---|
| £110 (the 1% hard limit) | one 1d position consumes the host's whole open-risk room (research E3: open risk ≤ 0.5 x the remaining allowance, so £110 at the day start) for up to 6 days. That blocks about 1,000 scalp and intraday labels for each swing label | rejected |
| £55 (0.5%; survives the SIZE_DOWN halving) | still takes half the host's open-risk room | rejected |
| **£40** | a native 4h stop fits (about $50, £38). 1d and macro stops above about $53 are skipped with reason `arm_cap` and stay history-only (H-60) | **chosen** |

Swing gives 2-3 labels a day at most. Its live value is swap and multi-day fill truth, which one position measures.

**Within a sleeve:**

| Rule | Detail |
|---|---|
| Controls get a fixed share, not a bandit share | S0 25% of the scalp sleeve (£12.50), I0 30% of the intraday sleeve (£14.40), W0 one swing entry in three. Controls are the null and must not be allocated by results |
| Strategy arms | split the rest by H-61 Thompson shares (floor 5%, cap 30% of the sleeve) |
| Per-arm £ cap | the lower of its share and its research-table cap |
| Admission order | each refusal at a sleeve, slot or host cap is journalled with its reason and propensity (research E6), so the off-policy estimates stay unbiased |

**Concentration** (tighten-only, all sleeves):

| Rule | Limit | Why |
|---|---|---|
| Net same-side exposure | ≤ 0.10 lot (10 oz) | a $20 gap through the stops then costs about £150, inside £220 |
| Weekend | no scalp or intraday entry whose maximum hold crosses the Friday close | only the single swing position (about £38 at the stop) carries weekend gap risk |

**Reallocation.** The split is the starting point, not a target. The CFO and the research director review it after
10 risk days, then monthly in the finance review. The review uses **demo £ per admissible label by sleeve** and per-arm
stops reached. Money moves only between sleeves. The total stays £198.

### 2. Rotation at -8% (decided)

**Trigger and pause:**

| Drawdown of icm-probe from its mark | Action |
|---|---|
| -6% (about £660) | Warning. The finance flag and Telegram reach the program director. The research director and CFO check that the bleeding arms have hit their H-61 stops. The program director queues the owner's rotation act (below) |
| **-8% (the gate's SIZE_DOWN)** | The exploration layer **pauses every M, scalp, intraday and swing entry**. P0 continues: a £15 sleeve is at most 0.14% a day, so going from 8% to 12% would take more than 6 weeks. Open trades exit by their own rules; exits are never gated. Without this pause the arms would keep bleeding into the 12% halt (K2: 40 days lost) |

**Options for the rotation:**

| Option | £ cost | Owner input | Effect on the mark | Verdict |
|---|---|---|---|---|
| R1 Owner rotates icm-probe: an account-scoped mark reset by TOTP | £0 | one TOTP action every 3-12 weeks | mark = equity, stage NORMAL; journal `rotation` with the epoch number | **chosen** (needs code: K3) |
| R2 Owner tops up the demo balance | £0 (demo money) | the broker's client area | under K4 the mark rises by the deposit, so the drawdown stays (-7.4%) | **optional add-on to R1**, only when equity is below £10,500: it keeps the £220 and £198 figures valid, because 2% of a lower day start cuts them pro rata. Top up first, then rotate |
| R3 Open a fresh demo account | £0 | sign-up, plus SRE work on a terminal and bridge | new account, new mark | **fallback only**: if the broker stops top-ups or expires the demo, or R1 is not built yet |
| R4 Let the deposit itself reset the mark on probe-only accounts | £0 | a top-up | mark = balance | **rejected**: it reverses ADR 0042 D3 and hides a reset inside a capital flow |
| R5 Do nothing | £0 | none | halt at 12%, then at least 40 days without learning | **rejected** |

**R1 conditions** (all coded, each with a named refusal):
- the account is `probe_only` and `mode: demo`, and the terminal reports demo;
- the drawdown is ≥ 8%;
- no open position other than P0;
- an owner TOTP;
- it touches no other engine and no dynamic-limit state;
- it never resets `day_start_equity` or `week_start_equity` (today's and this week's loss are not forgiven).

A code path that lowers a mark without the owner is not built. If it ever leaked to a live account, it would remove
the drawdown stages.

**Rotation budget** (program-level stop, recorded in the ledger):

| Rotations in a quarter | Rule |
|---|---|
| 1st | as above |
| 2nd | needs a written readout by the research director and CFO first: arms stopped, demo £ per label, any graduate in sight |
| 3rd | **not done.** Exploration stays paused until the quarter-freeze review |

Rotations hide the loss total, so the quarter's cumulative demo drawdown across epochs is reported, not just the
current epoch's.

**Accounting** (finance-auditor, monthly ledger):

| Item | How it is recorded |
|---|---|
| Top-up | a `deposit` capital flow (ADR 0042 D1/D2). Never P&L, R, phase-gate or north-star evidence. Monthly return stays time-weighted with flows excluded |
| Rotation | a £0 entry plus the journal `rotation` row, with the epoch number, the old and new mark, and the epoch's demo £ consumed by sleeve |
| Exploration loss | "exploration cost (demo £)", real cost **£0**. Kept apart from the book (research E9). It is not a running cost. ROI is read as demo £ per admissible label and graduates per quarter |
| High-water mark | top-up: `mark + flow` (ADR 0042 D3, unchanged). Rotation: `mark = equity`, the only other lowering besides the existing 12% re-arm |

### 3. Weekly cap for icm-probe: no change (6%, £660)

| Option | Effect | Verdict |
|---|---|---|
| **Keep 6%** | the worst week is 6% + a 2% overshoot day = 8% = the pause. With sleeve SD of about £61 a day (weekly about £137), it binds in about 4% of weeks at base cost and about 3% at pessimistic cost (pessimistic mean about £350 a week) | **chosen** |
| Tighten to 4% (£440) | binds in about 25% of weeks under pessimistic costs, losing Friday learning. Adds no protection: the -8% pause already keeps the account off the 12% halt | rejected |
| Loosen | never | n/a |

`daily_loss_limit_abs` 220, `budget_share` 0.9, ceilings daily 0.05 and `min_lot_risk_cap` 0.01 are unchanged.

**CFO tripwire** (a review trigger, not an engine limit): a realised weekly loss above £400 two weeks running brings
a sleeve review within 2 working days. ADR 0042's reversal (a) still stands.

**What would change this ADR:**
- The measured round-trip cost by sleeve after 10 risk days, if the pessimistic case holds. Scalp would then be cut to
  £30 and the money moved to intraday.
- A graduate arm (H-61): its sleeve gains share.
- The owner allowing a second probe account: return to the research program's per-style hosts. Swing gets its own
  £198, and its per-trade cap is reviewed up to the 1% hard limit.
- Two rotations in under 8 weeks: a program review.

## Consequences

- All about 24 arms fit one existing demo account with no new account and no £ spend. Expected volume is 85-90% of
  the design (scalp sleeve binding days, slot cap 18). Real cost: £0 cash. The H-60 historical matrix runs on GitHub
  Actions, free for a public repo.
- 1d and macro swing arms stay history-only on live demo until a second probe host exists. Live swing learning is one
  4h-sized position at a time.
- icm-probe never reaches its 12% halt by drift. It pauses at -8% and waits for one owner TOTP rotation every 3-12
  weeks (an optional top-up first). This is the one recurring owner touch in the program, kept on purpose: lowering a
  mark is the owner's.
- icm-demo, every live account and every RiskGate limit are unchanged.

### Implementation (implementer, failing tests first) and reviews

| # | Change | Review |
|---|---|---|
| X1 | Sleeve, per-arm and slot caps (section 1) in the exploration layer before the RiskGate (research E3). Measure = realised loss + open risk + new risk. Refusals are journalled with propensity. Fails closed on an unknown P&L or risk | **trading-safety** (order path), quant |
| X2 | Swing per-trade cap £40 and 1 swing slot; refusal `arm_cap` | **trading-safety** |
| X3 | Net same-side ≤ 0.10 lot; no scalp or intraday entry whose maximum hold crosses the Friday close | **trading-safety** |
| X4 | Pause at drawdown ≥ 8% on `probe_only` accounts: M and all arms refused (`exploration_paused_dd`), P0 continues; warning at -6% | **trading-safety** |
| X5 | Account-scoped rotation command: owner TOTP, `probe_only` + demo + terminal demo, drawdown ≥ 8%, flat except P0. Sets mark = equity and stage NORMAL. Leaves day and week starts and other engines' limits untouched. Journal `rotation`; quarter counter refuses a 3rd (`rotation_budget`). Never available on a live or non-probe account (refused at load and at the call) | **trading-safety** (full), security (TOTP path), code |
| X6 | Ledger: epoch rows, exploration cost by sleeve, rotation entries, top-ups as capital flows | finance-auditor |
| X7 | Dashboard and Telegram: icm-probe drawdown against -6% and -8%, the sleeves' £ used, the rotation count | ui-ux, code |

## Amendment 2026-10-26 (PD-approved, architect option c): per-style hosts on the two demo accounts

Context: section 1 put every arm on icm-probe because only one probe account existed; "What would change this ADR"
names a second probe account as the trigger to return to the research program's per-style hosts. The owner said to use
the two existing demo accounts. On one host the 1m/5m scalp arms fill most of the 18 slots and the shared £138, so the
15m-1d arms (the ones with a realistic graduation path at H-61's 300 trades) get few fills.

Decision (config only, `config/accounts.yaml`; ADR 0037 amendment 6 lets exploration alone make a `probe_only` host):

| Host | Arms | Exploration £ | Slots | Cost probes |
|---|---|---|---|---|
| icm-probe | scalp: S0, S0R, S1A, S1B, S2A, S2B, S3A, S3B, S4, S5A, S5B | scalp £138 (£50 + the £88 freed), 0.6273 x 220 | max_open_total 17 (one of 18 kept for the scheduled probe), scalp pot 8 | unchanged: P0 £15 + M £45 = £60 |
| icm-demo | intraday: I0, I2A, I2B, MB15, MB30, MB1H, MSA15; swing: W0, W1A, W1B, W2 | £198 = 0.9 x 220: intraday £108, swing £90 (section 1's 48 : 40, scaled) | max_open_total 17 of 18 (`max_positions: 18`), intraday 5, swing 1 | none (`cost_probe` off, `shadow_host` false) |

- No arm runs on two hosts (a test asserts disjoint arm sets across enabled accounts), so no arm's evidence is split
  across accounts or double-counted, and each arm's candidate and order ids hash its own account.
- Per-trade caps, controls' fixed shares (I0 30%, W0 33%), the X3 same-side and Friday rules, the X4 -8% pause and the
  RiskGate are unchanged. The exploration magic is `magic_base + 95` of each account (260195 and 260595).
- Swing's per-trade cap stays £40 and its slot 1; the review "up to the 1% hard limit" named above is a separate
  CFO + trading-safety decision, not taken here.
- icm-demo stops trading strategies: its paper evidence for the phase gate pauses. It returns to a strategy account at
  the first promotion verdict (RUNBOOK "icm-demo learning host": exploration off, flat, rotation, `probe_only` false,
  a new evidence epoch, restart).

Consequences: the exploration budget rises from £138 to £336 a day across the two hosts (each still inside its own
£220 day and 0.9 share); both accounts are demo, so the real cash cost is £0. Weekly cap 6% and the -8% pause now apply
per host. Rollback: on icm-demo `exploration.enabled: false`, `probe_only: false`, drop `max_positions`; move the
intraday and swing arms and pots back to icm-probe; restart both engines. Reviews: trading-safety (a second order-
sending host), quant (host split of H-61).
