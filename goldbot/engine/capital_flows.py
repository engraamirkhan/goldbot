"""The high-water mark's rise rule and capital flows (BACKLOG 54 / ADR 0018, ADR 0042), moved out of engine/runner.py
(ADR 0023 ratchet): the engine calls `update_hwm` on every account read."""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable

import pandas as pd

from goldbot.risk import hwm

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine
    from goldbot.execution.broker import AccountInfo

log = logging.getLogger("goldbot.engine")
FLOW_LOOKBACK = pd.Timedelta(days=1)


def update_hwm(eng: Engine, acc: AccountInfo, now: pd.Timestamp) -> None:
    """BACKLOG 54 (ADR 0018): "a rise in balance_closed_hwm is applied only once confirmed: by the deal history
    (closed deals and balance operations since the last mark account for the rise within a tolerance), or by a
    second balance read that agrees; one disagreeing read never raises it, two agreeing reads always do". A
    deposit explains a rise and raises the mark. The mark is never lowered here (only /rearm resets it). An
    unconfirmed rise is read again on the next tick; a refused one is logged and recorded. The first read of an
    engine that has no mark yet sets it (and its deal-history anchor). ADR 0042: a deposit or withdrawal is a
    capital flow, applied first (`apply_capital_flows`), never a rise."""
    st = eng.state
    if st.balance_closed_hwm == 0:
        st.balance_closed_hwm = acc.balance
        eng._set_hwm(hwm.HwmConfirmation(account_id=eng.cfg.account_id, mark=acc.balance, mark_utc=now))
        eng._flows_through = eng._flows_through or now      # flows before this read are in the mark and starts
        return
    if eng._hwm.mark != st.balance_closed_hwm:          # the owner's re-arm reset the mark: candidate and anchor void
        eng._hwm = hwm.HwmConfirmation(account_id=eng.cfg.account_id, mark=st.balance_closed_hwm)
    # a broker class may declare its balance reads exact (the paper broker's own ledger): looked up on the class,
    # so a remote broker's attribute pass-through can never answer it
    exact = getattr(type(eng.broker), "balance_exact", False) is True
    read: dict[pd.Timestamp, pd.DataFrame] = {}

    def deals(since: pd.Timestamp) -> pd.DataFrame:      # one deal-history read per anchor per account read
        if since not in read:
            read[since] = eng.broker.deals_since(since)
        return read[since]
    if not exact:                                        # the paper broker has no deposits
        apply_capital_flows(eng, acc, now, deals)
    mark = st.balance_closed_hwm
    s = hwm.step(eng._hwm, mark, acc.balance, now, eng.gate.limits.hwm_rise_tolerance, deals, exact=exact)
    if s.expired is not None:
        log.warning("high-water mark not raised: balance read %.2f expired unconfirmed (older than %s)",
                    s.expired, hwm.CANDIDATE_MAX_AGE)
        eng.decisions.append({"ts": time.time(), "action": "hwm_rise_refused", "candidate": s.expired,
                               "balance": acc.balance, "mark": mark, "reason": "expired"})
    if s.dropped is not None:
        log.warning("high-water mark not raised: balance read %.2f was not confirmed (next read %.2f, mark %.2f)",
                    s.dropped, acc.balance, mark)
        eng.decisions.append({"ts": time.time(), "action": "hwm_rise_refused", "candidate": s.dropped,
                               "balance": acc.balance, "mark": mark})
    if s.outcome == "pending":
        log.warning("balance %.2f is above the high-water mark %.2f and is not confirmed by the deal history; "
                    "re-read next tick", acc.balance, mark)
        eng.decisions.append({"ts": time.time(), "action": "hwm_rise_unconfirmed", "balance": acc.balance,
                               "mark": mark})
    elif s.outcome == "raised":
        st.balance_closed_hwm = s.mark
        eng.decisions.append({"ts": time.time(), "action": "hwm_raised", "from": mark, "to": s.mark, "by": s.by,
                               "deposits": s.deposits})
        eng._save_risk_state_now("the raised high-water mark")   # survives a restart at once
    if s.outcome != "unchanged" or s.expired is not None:
        eng._set_hwm(s.conf)
    eng._reread_hwm = s.conf.pending_balance is not None

def apply_capital_flows(eng: Engine, acc: AccountInfo, now: pd.Timestamp,
                        deals: Callable[[pd.Timestamp], pd.DataFrame]) -> None:
    """ADR 0042 D1, D3, D4: "A deposit or withdrawal is a capital flow (MT5 DEAL_BALANCE). Never in P&L"; the mark
    becomes max(mark + net flow, balance) (`hwm.apply_flows`, then the rise rule); "on a confirmed balance
    operation day_start_equity and week_start_equity move by the same net flow, so the loss so far is neither
    forgiven nor created". A start moves only for a flow stamped after it was set (a flow before the roll is in the
    equity the roll took). The deal history is read when the balance changed since the last read of it, or while a
    rise above the mark is unconfirmed (a deposit whose deal shows up a read late). Unreadable: nothing applied,
    retried on the next read; a deposit then reads as profit until it is seen, a withdrawal as a loss.

    Each flow is journalled (`capital_flow`: amount and kind, no identifier) and the risk state saved at once.
    The finance ledger records the same balance operations from the broker's history (goldbot.ops.ledger RC-9,
    kind deposit / withdrawal)."""
    if eng._flows_through is None:
        # an older risk file (review of 14f154aa, HIGH): from the period rolls on, so a deposit the release before
        # folded into the mark at its restart still moves the starts; apply_flows skips flows the mark holds
        eng._flows_through = eng._week_reset or eng._last_reset or eng._hwm.mark_utc or now
    if acc.balance == eng._flow_balance and eng._hwm.pending_balance is None:
        return
    since = eng._flows_through
    try:
        flows = hwm.capital_flows(deals(since), since)
    except Exception as exc:
        log.warning("deal history unreadable for capital flows (%s); read again on the next account read",
                    type(exc).__name__)
        return
    eng._flow_balance = acc.balance
    # MT5 stamps a deal when it books it: a day's margin covers a late-visible deal, never re-applies one
    eng._flows_through = max([since, now - FLOW_LOOKBACK, *(f.ts_utc for f in flows)])
    if not flows:
        return
    st = eng.state
    before = st.balance_closed_hwm
    # tighten-only on anything but a demo account: a withdrawal lowers neither the mark nor the starts there
    lower = eng.cfg.mode == "demo"
    r = hwm.apply_flows(eng._hwm, acc.balance, eng.gate.limits.hwm_rise_tolerance, flows, deals, lower=lower)
    if r.applied:
        st.balance_closed_hwm = r.conf.mark
        eng._set_hwm(r.conf)
    elif r.reason is not None:
        log.warning("capital flow not applied to the high-water mark (%s): the ADR 0018 rule applies", r.reason)
    day_set = eng._last_reset
    week_set = eng._week_reset or eng._last_reset
    for f in flows:
        # a withdrawal lowers the starts only once the deal history reconciled it, and only on a demo account
        # (review of 14f154aa): otherwise it reads as a loss. A deposit always raises them (the strict side)
        moves = f.amount > 0 or (lower and r.applied)
        day = f.amount if moves and day_set is not None and f.ts_utc > day_set and st.day_start_equity > 0 else 0.0
        week = f.amount if moves and week_set is not None and f.ts_utc > week_set and st.week_start_equity > 0 \
            else 0.0
        st.day_start_equity = max(0.0, st.day_start_equity + day)
        st.week_start_equity = max(0.0, st.week_start_equity + week)
        log.warning("capital flow %+.2f (%s): high-water mark %.2f -> %.2f, day start %+.2f, week start %+.2f",
                    f.amount, "deposit" if f.amount > 0 else "withdrawal", before, st.balance_closed_hwm, day,
                    week)
        eng.decisions.append({"ts": time.time(), "action": "capital_flow",
                               "kind": "deposit" if f.amount > 0 else "withdrawal", "amount": f.amount,
                               "deal_utc": f.ts_utc.isoformat(), "mark_from": before,
                               "mark_to": st.balance_closed_hwm, "mark_applied": r.applied,
                               "day_start_shift": day, "week_start_shift": week})
    eng._save_risk_state_now("the capital flow")         # survives a restart at once: never applied twice
