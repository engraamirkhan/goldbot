---
number: 54
rank: 3
title: Confirm increases in balance_closed_hwm
stage: Execution and risk (drawdown stages) · PROTECT
size: S
status: 'done (2026-10-16: faf824c + review fixes d331861, c79c7e4; ADR 0018; trading-safety APPROVED on re-verify) · accountable: implementer · due 2026-10-23'
---
- **54. Confirm increases in balance_closed_hwm (trading-safety review of BACKLOG 44, 2026-10-12).** Value: the
  drawdown stages measure equity against `balance_closed_hwm`, which `_refresh_account` raises on every read
  (`max(hwm, balance)`) and saves. One inflated balance read (a glitch, a half-filled account struct) raises the
  high-water mark for good. Every later real read then looks like a drawdown: size-down at 8% and, with ADR 0016's
  two reads, a phantom 12% kill switch that closes everything at market and needs a 40-day re-arm. ADR 0016 confirms
  a collapsed equity read but trusts any balance read that raises the mark. Acceptance:
  - a rise in `balance_closed_hwm` is applied only once confirmed: by the deal history (closed deals and balance
    operations since the last mark account for the rise within a tolerance), or by a second balance read that agrees;
    one disagreeing read never raises it, two agreeing reads always do;
  - a deposit (a balance operation in the deal history) is handled explicitly and the choice recorded in ADR 0016 or a
    new ADR (raise the mark, or not);
  - the mark is never lowered by this rule (only `/rearm` resets it), and a refused rise is logged and recorded as a
    decision;
  - tests: a single inflated balance read leaves the mark unchanged and causes no size-down or halt on the next real
    reads; two agreeing reads, or a read the deal history explains, raise it as today; a restart between the two reads
    does not apply an unconfirmed rise.
  - **Done 2026-10-16:** ADR 0018; `goldbot/risk/hwm.py` (`step`, `HwmConfirmation` in core.db migration 2
    `hwm_confirmation`), runner `_update_hwm` (re-read next tick), `risk.hwm_rise_tolerance` (0.1%); deposits raise
    the mark; health `risk:<account>` warns on a rise waiting over 5 min; tests in `tests/test_hwm_confirmation.py`
    and `tests/test_persistence_compat.py::case_hwm_confirmation` (TRACEABILITY R40).
  - **Review fixes 2026-10-16 (trading-safety review of faf824c):** ADR renumbered 0018; M1 a candidate the later
    deals corroborate raises the mark (`candidate_deals`), paper broker `balance_exact`; M2 the first pending time is
    kept; L1 one core.db handle per engine, 500 ms write busy timeout; L2 candidates expire after 10 min; accepted
    knowns recorded in the ADR. Awaiting trading-safety re-review.
