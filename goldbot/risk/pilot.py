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

ADR 0106 decision 2 (the owner's real-money limits, goldbot/risk/real_money.py) supersedes rails 1, 2 and 4: the block
is refused at load when `max_risk_frac` is above 1 %, `daily_cap_frac` above 2 % or `drawdown_stages` is false, enabled
or not, and the RiskGate applies the 1 % / 2 % / 15 % limits after every pilot rule anyway. The amendment's weekly
cap (25 %) is restored to the standing 5 % (PD + CFO, review L3). Rail 3 (`leverage_cap`)
and the absolute terms (£3.12, £6.25, £25, 30 entries, 0.01 lot) stand: they are only ever tighter.
"""
from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import Field, model_validator

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.risk.real_money import RM_DAILY_STOP, RM_MAX_RISK_PER_TRADE

#: ADR 0055's terms: the bounds of `PilotRules`
PILOT_LOT = 0.01
PILOT_MAX_STOP_USD = 4.0
PILOT_MAX_TRADE_RISK = 3.12          # account currency (GBP), loss at the stop plus the round trip
PILOT_MAX_STOP_LOSS = 25.0           # account currency (GBP), cumulative realised loss
PILOT_MAX_DAILY_LOSS = 6.25          # account currency (GBP): the account's daily_loss_limit_abs, two full stops
PILOT_MAX_ENTRIES = 30               # filled entries
PILOT_MAX_LEVERAGE_CAP = 500.0
# ADR 0106 decision 2 supersedes ADR 0055's 0.125 (£3.12 of £25) and 0.25 (£6.25 of £25): never above the owner's
# real-money limits. At 1 % the pilot's £3.12 trade needs about £312 of equity; below that the gate refuses it
PILOT_MAX_RISK_FRAC = RM_MAX_RISK_PER_TRADE
PILOT_MAX_DAILY_FRAC = RM_DAILY_STOP
# ADR 0055's amendment allowed 0.25 (£12.50 at £50); restored to the standing 5 % by PD + CFO on the ADR 0106
# trading-safety review (L3, a tightening)
PILOT_MAX_WEEKLY_FRAC = 0.05


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
    # rail 4 (superseded, ADR 0106): the 8%/12% drawdown stages always run; False is refused at load
    drawdown_stages: bool = True
    # ADR 0055 amendment (trading-safety ruling): the weekly cap as a fraction of the week-start equity, replacing the
    # 5% weekly cap on this account only (5% = £2.50 at £50 would end the week after one full stop)
    weekly_cap_frac: float = Field(0.05, gt=0, le=PILOT_MAX_WEEKLY_FRAC)
    # ADR 0055 amendment: True = at the Friday cut the engine closes EVERY position of this account, winners too,
    # instead of the standing rule (close losers, tighten winners): a weekend gap on a £25-£50 account is the risk
    weekend_flat: bool = False

    @model_validator(mode="after")
    def _complete(self) -> PilotRules:
        if not self.drawdown_stages:         # judged enabled or not: a block that would turn the stages off is refused
            raise ValueError("drawdown_stages: false is refused (ADR 0106: the 15 % halt holds on real money)")
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
