"""The owner's real-money limits (ADR 0106 decision 2, 2026-11-26): "risk per trade <= 1 % of equity, daily loss limit
2 % of equity then stop for the day, max drawdown halt at 15 % (the review starts at 15 %, and the 20 % edge is never
reached), and order size <= about 1 % of the asset's daily volume. They are enforced in the RiskGate as plain code the
models cannot override."

The four figures are module constants (`Final`), never a settings key, an accounts.yaml field or a `RiskLimits` field:
no profile, override, controller, dynamic term, pilot block or model output can move them, and
tests/test_real_money_limits.py fails if any module outside this one assigns them. The RiskGate applies them after
every other limit, on any account whose `RiskLimits.real_money` is set (ops.services.engine.engine_limits: a live
account against its real terminal) or whose intent says it is not demo; a demo account is decided exactly as before.
Entries only: exits never call the gate.

* `RM_MAX_RISK_PER_TRADE`: the sized trade's loss at the stop plus its round trip and swap (`GateDecision.risk_amount
  + cost_amount`) above 1 % of equity is refused `rm_risk_per_trade`, never shrunk;
* `RM_DAILY_STOP`: each entry's loss at the stop plus its round trip is capped at what is left of the day's 2 % after
  the loss so far and the open risk (`daily_headroom`; sized down in whole steps, refused `rm_daily_projected` below
  the minimum lot), and a loss from the day-start equity (realised and open) that reaches 2 % at any read latches
  `AccountState.rm_daily_stop`; every entry is refused `rm_daily_stop` until the next risk-day roll (`gate.new_day`),
  even if the equity recovers. The latch is in the engine's risk file, so a restart keeps it;
* `RM_DD_HALT`: the drawdown stage machine halts at min(dd_stage2, 15 %) from the closed-balance high-water mark
  (the 12 % default still fires first), and a read at 15 % or more refuses entries `rm_drawdown_halt` on its own;
* `RM_MAX_ADV_SHARE`: the order's notional in the instrument's quote currency above 1 % of its reference average daily
  volume (config/data/adv_reference.yaml) is refused `rm_volume`; a reference that is missing, stale, unreadable or in
  another currency refuses `rm_volume_unknown` (fail closed).
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from pathlib import Path
from typing import Final

import yaml
from pydantic import Field, ValidationError, model_validator

from goldbot.base import FrozenRecord
from goldbot.config import ROOT

log = logging.getLogger(__name__)

#: ADR 0106 decision 2: the owner's limits on real money. Tightening one is a code change with review; loosening one
#: needs a new owner decision recorded as an ADR first
RM_MAX_RISK_PER_TRADE: Final[float] = 0.01     # of equity, loss at the stop plus the round trip
RM_DAILY_STOP: Final[float] = 0.02             # of the day-start equity, realised plus open; then stop for the day
RM_DD_HALT: Final[float] = 0.15                # from the closed-balance high-water mark
RM_MAX_ADV_SHARE: Final[float] = 0.01          # of the instrument's reference average daily volume (notional)

#: the reference daily volumes (data-engineer owned: each value with its source and date)
ADV_REFERENCE_FILE: Final[Path] = ROOT / "config" / "data" / "adv_reference.yaml"
#: a reference older than this is unknown (refused on real money): volumes move with the market, so a year-old figure
#: is re-measured, never trusted
ADV_MAX_AGE_DAYS: Final[int] = 400


class AdvReference(FrozenRecord):
    """One instrument's reference average daily volume, as notional in its quote currency per day (e.g. COMEX GC:
    contracts a day x 100 oz x the settlement price, USD). `adv_notional` None = not filled yet (unknown). A value is
    accepted only with the source it was read from and the date it describes."""
    adv_notional: float | None = Field(None, gt=0)
    currency: str = Field(min_length=3, max_length=3)
    source: str = ""
    as_of: dt.date | None = None

    @model_validator(mode="after")
    def _sourced(self) -> AdvReference:
        if self.adv_notional is not None and (not self.source.strip() or self.as_of is None):
            raise ValueError("a reference daily volume needs its source and as_of date")
        return self


def load_adv_reference(path: Path = ADV_REFERENCE_FILE, *, today: dt.date | None = None) -> dict[str, AdvReference]:
    """The usable references by instrument (registry key): filled, sourced and no older than ADV_MAX_AGE_DAYS on
    `today` (default: the UTC date). A missing or unreadable file, or an entry that does not validate, gives no
    reference for it (the gate then refuses real-money entries `rm_volume_unknown`); never raises."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    try:
        raw = yaml.safe_load(path.read_text())
        entries = raw["instruments"] if isinstance(raw, dict) else None
        if not isinstance(entries, dict):
            raise ValueError("no instruments map")
    except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        log.error("reference daily volumes unreadable (%s): real-money entries refused", type(exc).__name__)
        return {}
    out: dict[str, AdvReference] = {}
    for sym, body in entries.items():
        try:
            ref = AdvReference.model_validate(body)
        except ValidationError as exc:
            log.error("reference daily volume of %s refused (%s): real-money entries refused", sym, exc.error_count())
            continue
        if ref.adv_notional is None or ref.as_of is None:
            continue
        if (today - ref.as_of).days > ADV_MAX_AGE_DAYS:
            log.warning("reference daily volume of %s is from %s, older than %d days: unknown", sym, ref.as_of,
                        ADV_MAX_AGE_DAYS)
            continue
        out[str(sym)] = ref
    return out


def daily_stop_reached(equity: float, day_start_equity: float) -> bool:
    """The loss from the day start (realised and open: equity is balance plus open P&L) at or beyond RM_DAILY_STOP.
    False while the day start is unknown (the gate refuses that on its own)."""
    return day_start_equity > 0 and 1 - equity / day_start_equity >= RM_DAILY_STOP - 1e-12


def daily_headroom(equity: float, day_start_equity: float, open_risk: float) -> float:
    """What is left of the day's RM_DAILY_STOP (account currency): 2 % of the day start, less the loss so far (equity
    is balance plus open P&L) and the open positions' risk to their stops. 0 or less: no entry fits."""
    return RM_DAILY_STOP * day_start_equity - max(0.0, day_start_equity - equity) - max(0.0, open_risk)


def drawdown_halt_reached(equity: float, balance_closed_hwm: float) -> bool:
    return balance_closed_hwm > 0 and 1 - equity / balance_closed_hwm >= RM_DD_HALT - 1e-12


def trade_risk_exceeded(risk_amount: float, cost_amount: float, equity: float) -> bool:
    """The sized trade's loss at the stop plus its round trip above RM_MAX_RISK_PER_TRADE of equity; exactly at it is
    allowed (a relative 1e-9 absorbs float noise)."""
    return risk_amount + max(0.0, cost_amount) > RM_MAX_RISK_PER_TRADE * equity * (1 + 1e-9)


def volume_known(ref: AdvReference | None, currency: str) -> bool:
    """A usable reference in the order's quote currency (else the gate refuses `rm_volume_unknown`)."""
    return ref is not None and ref.adv_notional is not None and math.isfinite(ref.adv_notional) \
        and ref.currency.upper() == currency.strip().upper()


def volume_exceeded(notional: float, ref: AdvReference) -> bool:
    """The order's notional (quote currency) above RM_MAX_ADV_SHARE of the reference; exactly at it is allowed."""
    return notional > RM_MAX_ADV_SHARE * (ref.adv_notional or 0.0) * (1 + 1e-9)
