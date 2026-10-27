"""The live pilot's rails (ADR 0055: "Option (a) for account `icm-live` only. Every other account and every default is
unchanged").

`PilotRules` is the `pilot:` block of one live account in config/accounts.yaml. The RiskGate enforces it for that
account alone (goldbot/risk/gate.py `_pilot_*`), engine_limits turns its four changed rails into that account's limits
(goldbot/ops/services/engine.py), the bridge takes its notional ceiling from it (goldbot/ops/services/bridge.py), and
the engine keeps `PilotState` from the broker's deal history in core.db (goldbot/engine/pilot.py).

The ADR's terms: "0.01 exactly ... there is no sizing-up"; "Stop distance at most $4.00 from entry. A longer stop is
refused, never shrunk"; "Risk per trade at most £3.12 at the stop, including the round trip"; "after £25 cumulative
realised loss or 30 filled entries, whichever comes first: entries halt for good. Open positions keep their automatic
exits"; "Open positions at most 1"; "15m families only"; "`leverage_cap` 500 for this account (not the broker's
1:5000)". The field bounds are those terms: widening one needs a new recorded decision, never only a config edit (as
`limits_profile.RiskCeilings`).

"Rails changed for this account only ... Each is a loosening of a standing rail": (1) the per-trade 1% becomes £3.12
absolute (`max_risk_frac` lets it bind down to £25 of equity); (2) the 2% daily cap becomes £6.25 absolute
(`daily_cap_frac`, so the account's `daily_loss_limit_abs` binds); (3) `leverage_cap` 20 -> 500; (4) the drawdown
kill gives way to the pilot stop (`drawdown_stages: false`). Each is a config value read by engine_limits for the pilot
account only; none is a code path that skips a check.
"""
from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import Field, model_validator

from goldbot.base import FrozenRecord, UtcTimestamp

#: ADR 0055's terms: the bounds of `PilotRules`
PILOT_LOT = 0.01
PILOT_MAX_STOP_USD = 4.0
PILOT_MAX_TRADE_RISK = 3.12          # account currency (GBP), loss at the stop plus the round trip
PILOT_MAX_STOP_LOSS = 25.0           # account currency (GBP), cumulative realised loss
PILOT_MAX_DAILY_LOSS = 6.25          # account currency (GBP): the account's daily_loss_limit_abs, two full stops
PILOT_MAX_ENTRIES = 30               # filled entries
PILOT_MAX_LEVERAGE_CAP = 500.0
PILOT_MAX_RISK_FRAC = 0.125          # £3.12 of £25: the per-trade £ cap binds down to the pilot's lowest equity
PILOT_MAX_DAILY_FRAC = 0.25          # £6.25 of £25: the absolute daily limit binds down to the pilot's lowest equity


class PilotRules(FrozenRecord):
    """One live account's pilot. Off unless set; a disabled block is inert and not judged. Amounts are in the account's
    deposit currency, as `daily_loss_limit_abs`."""
    enabled: bool = False
    adr: int | None = None                   # the decision record of these terms (55)
    # names this pilot run: its stop belongs to one id (core.db pilot_state), so a run after a stop is a new id
    # under a new recorded decision, never a reset of the old one
    pilot_id: str = Field("", pattern=r"^$|^[a-z0-9][a-z0-9-]{2,39}$")
    # deals stamped from here on are the pilot's (its realised loss and filled entries); set before the first order
    start_utc: UtcTimestamp | None = None
    lot: float = Field(PILOT_LOT, gt=0, le=PILOT_LOT)
    max_stop_usd: float = Field(PILOT_MAX_STOP_USD, gt=0, le=PILOT_MAX_STOP_USD)
    max_risk_per_trade_abs: float = Field(PILOT_MAX_TRADE_RISK, gt=0, le=PILOT_MAX_TRADE_RISK)
    stop_loss_abs: float = Field(PILOT_MAX_STOP_LOSS, gt=0, le=PILOT_MAX_STOP_LOSS)
    max_entries: int = Field(PILOT_MAX_ENTRIES, ge=1, le=PILOT_MAX_ENTRIES)
    timeframes: tuple[str, ...] = ("15m",)
    max_positions: int = Field(1, ge=1, le=1)
    # rail 3: the gate's margin and combined-notional figure, and the bridge's notional ceiling (0.30 x this x
    # equity); the terminal's own leverage applies when it is lower
    leverage_cap: float = Field(20.0, gt=0, le=PILOT_MAX_LEVERAGE_CAP)
    # rail 1: the per-trade maximum as a fraction of equity (the £ cap above binds first down to £25)
    max_risk_frac: float = Field(0.01, gt=0, le=PILOT_MAX_RISK_FRAC)
    # rail 2: the daily cap as a fraction of the day-start equity (the account's daily_loss_limit_abs binds first)
    daily_cap_frac: float = Field(0.02, gt=0, le=PILOT_MAX_DAILY_FRAC)
    # rail 4: False = the 8%/12% drawdown stages do not run on this account; the pilot stop is the hard halt
    drawdown_stages: bool = True

    @model_validator(mode="after")
    def _complete(self) -> PilotRules:
        if not self.enabled:
            return self
        if not self.pilot_id or self.start_utc is None:
            raise ValueError("an enabled pilot needs pilot_id and start_utc")
        if self.max_risk_per_trade_abs > self.stop_loss_abs:
            raise ValueError("pilot max_risk_per_trade_abs must be at most its stop_loss_abs")
        if not self.timeframes:
            raise ValueError("an enabled pilot names at least one timeframe")
        return self


StopReason = Literal["stop_loss", "max_entries"]


class PilotState(FrozenRecord):
    """The engine's record of the pilot (goldbot/engine/pilot.py; core.db `pilot_state`, one row per account). Counted
    from the broker's deal history since `start_utc`; `realised_loss` / `entries` None = not read (the RiskGate refuses
    entries on it, fail closed)."""
    account_id: str
    pilot_id: str
    realised_loss: float | None = None       # max(0, -(net P&L, commission, swap and fees of trade deals))
    entries: int | None = None               # filled entries (MT5 DEAL_ENTRY_IN buys and sells), any magic
    stopped_utc: UtcTimestamp | None = None  # when the stop was reached; sticky for this pilot_id
    stop_reason: StopReason | None = None
    read_utc: UtcTimestamp | None = None     # the last deal-history read

    @property
    def known(self) -> bool:
        return self.realised_loss is not None and self.entries is not None


def stop_reason(rules: PilotRules, loss: float, entries: int) -> StopReason | None:
    """Which stop the counts reached: the £ stop first, then the 30th filled entry."""
    if loss >= rules.stop_loss_abs - 1e-9:
        return "stop_loss"
    if entries >= rules.max_entries:
        return "max_entries"
    return None


def pilot_start(rules: PilotRules) -> pd.Timestamp:
    assert rules.start_utc is not None
    return pd.Timestamp(rules.start_utc)
