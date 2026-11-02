"""The closed-trade record of one fully closed position (`Engine._record_closed`) and its MAE / MFE fields
(`Engine._closed_excursion`), moved verbatim out of engine/runner.py (ADR 0023 runner split; multi-instrument lane L3
needed the line room). Behaviour is unchanged: the runner's methods call these.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from goldbot.engine import cost_probe
from goldbot.labels.excursion import Excursion
from goldbot.ops.gates_phase import ClosedTrade, EntryAudit
from goldbot.risk import sizing

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine
    from goldbot.engine.store import OpenTrade

log = logging.getLogger("goldbot.engine")
# a position frozen at its broker stop or target (MAE/MFE, BACKLOG 23) that exits later than this was not taken there:
# the path after the freeze was not seen and the record says approximate
EXCURSION_FREEZE_TOLERANCE_S = 60.0


def record_closed(eng: Engine, pid: int, tr: OpenTrade, reason: str, px: float | None, at: pd.Timestamp) -> None:
    """One ClosedTrade for a fully closed position (scale-out folded in: see gates_phase.ClosedTrade), appended
    durably to state/closed_trades.jsonl. Money from the broker's deals when it reports them (net P&L with
    commission and swap); otherwise from the engine's own fills at the contract size (commission and swap None).
    Finance audit: `money_source` says which, and `pnl_basis` is "gross" unless commission and swap are both known
    (GAP-05), and `ret_basis` is "price" when ret could not be taken from that P&L (a non-USD account with no
    conversion: quant review 2026-10-12, finding B); the stops (GAP-03) and the entry facts (`OpenTrade.audit`:
    GAP-01/02/13) go on the record, and a broker stop or target fill is logged against its level in `exit_fills`
    (GAP-04)."""
    from goldbot.engine import runner
    if pid in eng._recorded or cost_probe.record_close(eng, pid, tr, reason, px, at):   # ADR 0037: own ledger
        return
    try:
        info, _ = eng._exit_deals(pid, tr)
    except Exception:
        log.exception("deal history unreadable for position %s; recorded from the engine's fills", pid)
        info = None
    known = info or {}
    # neither the broker's deals nor the engine's own close fill: the price is the quote read now and the time the
    # moment the engine noticed, neither of them the trade's (MAE/MFE never folds them, BACKLOG 23)
    synthetic = info is None and px is None
    entry = tr.open_price if tr.open_price is not None else known.get("entry_price") or tr.entry
    if entry is None:
        raise ValueError("entry price unknown")
    lots = float(tr.initial_lots or known.get("lots") or round(tr.lots + tr.scaled_lots, 8))
    contract = 100.0
    try:
        contract = float(eng.broker.symbol_info(eng.cfg.symbol).contract_size) or contract
    except Exception:
        log.warning("symbol info unavailable; position %s recorded at contract size %s", pid, contract)
    # BACKLOG 28 (ADR 0004 A5): risk and notional are USD (price x lots x contract); the broker's P&L is in the
    # deposit currency. fx converts USD into it (1 on a USD account); None when the broker cannot say
    dist = abs(entry - tr.initial_sl) if tr.initial_sl is not None else 0.0
    fx = sizing.account_fx(eng._account_currency(), eng._usd_to_account(tr.side, entry, dist or 1.0))
    risk_usd, notional_usd = dist * lots * contract, entry * lots * contract
    # the P&L's currency on the record (dashboard contract): the account currency when the broker's P&L is used or
    # the engine's was converted, "USD" for the unconverted own-fills fallback; None is left to legacy records
    # (the monthly report flags those as R understated), and when the account currency itself is unknown
    pnl_currency: str | None = eng._account_currency() or None
    ret_basis: Literal["pnl", "price"] = "pnl"     # ClosedTrade.ret_basis: "price" only when ret is not from pnl
    if info is not None:                          # pnl from the broker's deals, in the account currency
        exit_px, exit_utc, pnl = info["exit_price"], info["exit_utc"], info["pnl"]
        commission, swap = info["commission"], info["swap"]
        if reason == "broker_close" and info["reason"]:
            reason = info["reason"]
        if fx is not None:
            r = pnl / (risk_usd * fx) if risk_usd > 0 else None
            ret = pnl / (notional_usd * fx)
        elif eng._account_currency() == "USD":    # the notional is already in the account currency (finding B)
            log.warning("position %s: the broker's USD conversion is off; R left empty, return from the P&L", pid)
            r, ret = None, pnl / notional_usd
        else:                                      # no conversion: R unknown (never a wrong R); gross price return,
            log.warning("position %s: no USD -> %s conversion; R left empty, return from prices", pid,
                        eng._account_currency() or "account currency")
            r, ret, ret_basis = None, tr.side * (exit_px - entry) / entry, "price"   # flagged on the record
    else:                                          # the engine's own fills: pnl in USD, converted for the record
        if px is None:
            tick = eng.broker.last_tick(eng.cfg.symbol)
            px = tick.bid if tr.side > 0 else tick.ask
        legs = [(tr.lots, px)] + ([(tr.scaled_lots, tr.scaled_price)] if tr.scaled_price is not None else [])
        done = sum(n for n, _ in legs)
        exit_px = sum(n * p for n, p in legs) / done if done > 0 else px
        pnl_usd = sum(tr.side * (p - entry) * n * contract for n, p in legs)
        r = pnl_usd / risk_usd if risk_usd > 0 else None
        ret = pnl_usd / notional_usd
        if fx is None:                             # never read as factor 1: recorded in USD and flagged (LOW-1)
            log.warning("position %s: no USD -> account currency conversion; P&L recorded in USD", pid)
            pnl, pnl_currency = pnl_usd, "USD"
        else:
            pnl = pnl_usd * fx
        exit_utc, commission, swap = at, None, None
    equity = tr.equity_before
    if equity is None or equity <= 0:
        # the balance before this trade's P&L was realised (a USD-flagged P&L is not subtracted from a £ balance)
        usd_only = pnl_currency == "USD" and eng._account_currency() != "USD"
        equity = eng.broker.account().balance - (0.0 if usd_only else pnl)
        if equity <= 0:
            equity = eng.state.equity
    rec = ClosedTrade(account_id=eng.cfg.account_id, broker=eng.cfg.broker_name,
                      mode="live" if eng.cfg.mode == "live" else "demo", exit_utc=exit_utc,
                      ret=ret, pnl=pnl, equity_before=equity, position_id=pid,
                      client_order_id=tr.client_order_id, agent_id=tr.agent_id, side=tr.side, lots=lots,
                      entry_utc=tr.opened_utc or tr.entry_bar_ts, entry_price=entry, exit_price=exit_px,
                      exit_reason=reason, r=r, commission=commission, swap=swap, pnl_currency=pnl_currency,
                      partial_lots=tr.scaled_lots,
                      # finance audit: GAP-03 stops, GAP-05 where the money came from and whether it is net
                      initial_sl=tr.initial_sl if tr.risk_known else None, final_sl=tr.sl, final_tp=tr.tp,
                      money_source="broker" if info is not None else "engine",
                      pnl_basis="net" if commission is not None and swap is not None else "gross",
                      ret_basis=ret_basis,
                      **(tr.audit or EntryAudit(timeframe=tr.timeframe)).model_dump(),
                      **closed_excursion(eng, pid, tr, entry, tr.opened_utc or tr.entry_bar_ts, exit_utc,
                                               None if synthetic else px if px is not None else exit_px,
                                               exit_known=not synthetic))
    runner.append_closed_trade(Path(eng.cfg.state_dir), rec)   # the runner's name: tests patch it there
    level = {"stop": tr.sl, "target": tr.tp}.get(info["reason"]) if info is not None else None
    if info is not None and level is not None:     # GAP-04: a broker stop or target fill against its level
        eng._log_exit_fill(pid, tr, info["reason"], info["last_lots"], info["last_price"], at=exit_utc,
                            level=level)
    eng._recorded.add(pid)
    eng._throttle_closed.append(rec)
    log.info("closed trade recorded: position %s %s, pnl %.2f", pid, reason, pnl)   # the exit itself is journalled


def closed_excursion(eng: Engine, pid: int, tr: OpenTrade, entry: float, entry_utc: pd.Timestamp,
                      exit_utc: pd.Timestamp, exit_fill: float | None, *, exit_known: bool = True) -> dict[str, Any]:
    """The ClosedTrade MAE / MFE fields: the tick track plus the exit fill, every extreme at or before `exit_utc`.
    The record is approximate, and the stored 1m bars wholly inside the trade on the exit side are folded in, when
    ticks did not see the whole path: `excursion_approx` (a restart, an adopted position), no track at all, the
    position outlived a freeze at its broker level by more than EXCURSION_FREEZE_TOLERANCE_S, or `exit_known` is
    False (no deals and no close fill: the bars stop at the last tick folded and no exit price is folded). A track
    with an extreme after the exit (a manual or broker close seen late) is dropped and rebuilt from the bars up to
    the exit. R is left empty when the initial stop is not the trade's (`risk_known`). With no usable track and
    no bar folded (a close confirmed without deals and never tracked, or a dropped track with no covering bar)
    nothing of the path was observed: the fields stay empty rather than reading 0 R / 0 R (an exit fill alone is
    not an excursion). Never raises: a failure leaves the fields empty (the trade is still recorded)."""
    try:
        track = tr.excursion
        approx = tr.excursion_approx or track is None or not exit_known
        bars_end = exit_utc
        if not exit_known:
            exit_fill = None
            bars_end = track.last_utc if track is not None and track.last_utc is not None else entry_utc
        if track is not None and track.frozen_utc is not None and exit_known and \
                (exit_utc - track.frozen_utc).total_seconds() > EXCURSION_FREEZE_TOLERANCE_S:
            approx = True
        if track is not None and (track.mae_utc > exit_utc or track.mfe_utc > exit_utc):
            track, approx = None, True
        ex = track.model_copy() if track is not None else Excursion.start(entry, entry_utc)
        fav_col, adv_col = ("bid_high", "bid_low") if tr.side > 0 else ("ask_low", "ask_high")
        b = eng.bars_1m
        folded = 0
        if approx and not b.empty and {"ts_utc", fav_col, adv_col} <= set(b.columns):
            ts = pd.to_datetime(b["ts_utc"], utc=True)
            inside = b[(ts >= entry_utc) & (ts + pd.Timedelta(minutes=1) <= bars_end)]
            for at, fav, adv in zip(pd.to_datetime(inside["ts_utc"], utc=True), inside[fav_col], inside[adv_col]):
                ex.fold(tr.side, float(adv), at, float(fav), at)
                folded += 1
        if track is None and folded == 0:
            return {}
        if exit_fill is not None:
            ex.fold(tr.side, float(exit_fill), exit_utc)
        out = ex.summary(tr.side, entry, tr.initial_sl if tr.risk_known else None)
        return {**out, "excursion_approx": approx}
    except Exception:
        log.warning("MAE/MFE not recorded for position %s; the trade is recorded without it", pid, exc_info=True)
        return {}
