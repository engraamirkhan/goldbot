"""The closed-trade record of one fully closed position (`Engine._record_closed`) and its MAE / MFE fields
(`Engine._closed_excursion`), and the queue in front of it (`_note_closed`, `_flush_closed`, `_sweep_closed`,
`_exit_deals`), moved verbatim out of engine/runner.py (ADR 0023 runner split; multi-instrument lane L3 needed the line
room). Behaviour is unchanged: the runner's methods call these.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from goldbot.engine import cost_probe, terminal_account
from goldbot.engine.store import PendingClose
from goldbot.execution.deals import exit_summary
from goldbot.labels.excursion import Excursion
from goldbot.ops.gates_phase import ClosedTrade, EntryAudit
from goldbot.risk import sizing

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine
    from goldbot.engine.store import OpenTrade
    from goldbot.execution.broker import OrderResult

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
    contract = eng.cfg.contract_size          # the instrument's verified term (100 oz for XAUUSD), record only
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
        # R1: while the terminal is on another account its balance is never this account's (engine/terminal_account)
        equity = eng.state.equity if terminal_account.changed(eng) else \
            eng.broker.account().balance - (0.0 if usd_only else pnl)
        if equity <= 0:
            equity = eng.state.equity
    rec = ClosedTrade(account_id=eng.cfg.account_id, broker=eng.cfg.broker_name,
                      symbol=None if eng.cfg.instrument == "XAUUSD" else eng.cfg.instrument,
                      mode="live" if eng.cfg.mode == "live" else "demo", exit_utc=exit_utc,
                      ret=ret, pnl=pnl, equity_before=equity, position_id=pid,
                      client_order_id=tr.client_order_id, agent_id=tr.agent_id, side=tr.side, lots=lots,
                      entry_utc=tr.opened_utc or tr.entry_bar_ts, entry_price=entry, exit_price=exit_px,
                      exit_reason=reason, r=r, commission=commission, swap=swap, pnl_currency=pnl_currency,
                      partial_lots=tr.scaled_lots, downtime_bars=tr.downtime_bars or None,
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


def sweep_closed(eng: Engine, live: set[int]) -> None:
    """Open trades the broker no longer lists closed at the broker (stop, target, stop-out, manual): each is
    recorded once its exit deal is seen and then forgotten. When the broker's deal history carries positions but
    shows no exit, or is empty (a positions() reply that dropped the trade, or a deal not synced yet), the trade is
    kept as it is and asked again at the next bar close, `close_confirm_checks` times, then recorded from what the
    engine knows. An unreadable deal history (the read raised: MT5 history_deals_get returned None) never confirms
    a close: the trade is kept, uncounted, until the history can be read. Only called with a positions() reply
    that was read successfully, so an unreadable book never records or forgets a trade. A trade that reappears
    simply carries on with its stop and policy."""
    eng._unconfirmed = {k: v for k, v in eng._unconfirmed.items() if k in eng.open and k not in live}
    missing = [k for k in eng.open if k not in live]
    if not missing:
        eng._read_fails["deals"] = 0       # no deal read outstanding: a past failure no longer blocks entries
    for pid in missing:
        tr = eng.open[pid]
        try:
            summary, _ = eng._exit_deals(pid, tr)
        except Exception:                   # counted by _read_deals (positions_unreadable); asked again next pass
            continue
        tries = eng._unconfirmed.get(pid, 0) + 1
        if summary is None and tries < eng.cfg.close_confirm_checks:
            if tries == 1:
                eng.decisions.append({"ts": time.time(), "agent": tr.agent_id, "action": "close_unconfirmed",
                                       "position": pid})
            eng._unconfirmed[pid] = tries
            continue
        eng._unconfirmed.pop(pid, None)
        del eng.open[pid]
        eng._note_closed(pid, tr, "broker_close")


def note_closed(eng: Engine, pid: int, tr: OpenTrade, reason: str, res: OrderResult | None = None) -> None:
    """Queue the record of a fully closed position. Nothing is read or written here, so an exit never waits on the
    record: the queue is written after the tick's exits (`on_tick`) and before the open-trade table is saved."""
    if pid in eng._recorded or any(c.position_id == pid for c in eng._closes):
        return
    px = res.price if res is not None and res.ok else None
    at = eng._last_tick.ts_utc if eng._last_tick is not None else pd.Timestamp.now("UTC")
    eng._closes.append(PendingClose(position_id=pid, trade=tr, reason=reason, price=px, at=at))


def flush_closed(eng: Engine) -> None:
    """Write the queued records. A failure is logged and journalled (`closed_trade_record_failed`) and never
    raised: the exit has already happened and nothing after it may stop. The record stays queued (and in the
    open-trade table file, so a restart keeps it) and is retried every reconcile_every_s of tick time,
    `closed_record_retries` attempts in all; then it is reported lost (`closed_trade_record_lost` in the journal,
    `closed_records_lost` in the engine state, which fails the engine's health check and alerts the owner)."""
    if not eng._closes:
        eng._flush_exit_fills()
        return
    now = eng._last_tick.ts_utc if eng._last_tick is not None else pd.Timestamp.now("UTC")
    pending, eng._closes = eng._closes, []
    keep: list[PendingClose] = []
    for c in pending:
        if c.next_try is not None and (eng._last_tick is None or now < c.next_try):
            keep.append(c)                  # a retry waits for its time on the tick clock
            continue
        try:
            eng._record_closed(c.position_id, c.trade, c.reason, c.price, c.at)
        except Exception as e:
            c.tries += 1
            lost = c.tries >= eng.cfg.closed_record_retries
            log.exception("closed trade not recorded for position %s (%s), attempt %d", c.position_id, c.reason,
                          c.tries)
            eng.decisions.append({"ts": time.time(), "agent": c.trade.agent_id, "action": "closed_trade_record_failed",
                                   "position": c.position_id, "reason": c.reason, "tries": c.tries,
                                   "error": repr(e)})
            if lost:
                log.error("closed trade record LOST for position %s after %d attempts: %s", c.position_id,
                          c.tries, c.model_dump_json())
                eng.decisions.append({"ts": time.time(), "agent": c.trade.agent_id,
                                       "action": "closed_trade_record_lost", "position": c.position_id,
                                       "reason": c.reason, "pending": c.model_dump(mode="json")})
                eng._records_lost.append(c.position_id)
                eng._throttle_lost.append(c)
            else:
                c.next_try = now + pd.Timedelta(seconds=eng.cfg.reconcile_every_s)
                keep.append(c)
    eng._closes = keep + eng._closes
    eng._flush_exit_fills()             # after the records: a broker stop or target leg is added by its record


def exit_deals(eng: Engine, pid: int, tr: OpenTrade) -> tuple[dict | None, bool]:
    """(summary of the position's deals (`deals.exit_summary`: None when no exit deal is there), whether the
    broker's deal history identifies positions at all). A read that fails raises (`_read_deals`)."""
    starts = [x for x in (tr.opened_utc, tr.entry_bar_ts) if x is not None]
    d = eng._read_deals(min(starts) - pd.Timedelta(days=1))
    if not isinstance(d, pd.DataFrame) or "position_id" not in d.columns:
        return None, False
    return exit_summary(d, pid), True
