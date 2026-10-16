"""A conservative imputed cost for closed trades whose return is not net (quant review, 2026-10-12).

A closed record is "net" only when commission and swap are both known and inside `pnl` (`ClosedTrade.pnl_basis`), and
its `ret` is net only when it was computed from that P&L (`ClosedTrade.ret_basis`: "price" means the gross price return,
whatever `pnl_basis` says; a record without `ret_basis` whose money is the broker's, with the initial stop known and R
empty, is the same price return written before the field or by a rolled-back release: review R1). Every other row would read optimistically in the phase gates, the stop rule and the ruin
Monte Carlo. The review's rule: "Do NOT drop gross rows: their causes can be outcome-linked, so dropping them is
selection bias. Instead, charge each gross row a conservative imputed cost: the measured commission from the cost table
(or the prior), plus swap for every rollover held through (from broker terms or the swap prior, with the triple-swap day
counted)."

What is charged, per row, only for the components its return does not already carry:

* commission: both sides, at the account's nightly cost table (`state/costs_<account>.json`,
  `commission_per_lot_side_usd`: the terminal's measurement, else the settings value it was built with); without a
  table, the settings commission for the trade's broker (the largest configured one when the broker is not listed);
* swap: the paid rate for the trade's side times `labels.triple_barrier.rollover_nights` (one per weekday rollover held
  through, three on the triple day), at the cost table's measured rates, else the settings swap prior
  (`costs.swap_*`). A swap credit is never counted (conservative), on the server clock of the trade's broker; a row
  without a side pays the larger paid rate of long and short.

Both are USD per oz (a lot is `CONTRACT_OZ` ounces, as the cost tables assume), so the charge in return units is
cost per oz / entry price (the exit price when the entry is not recorded) and in R units cost per oz / |entry - initial
stop|; neither needs the lot size or a currency conversion. A row the charge cannot be computed for (no price at all,
or no entry time while swap is unknown) is "unpriced": it is kept, charged nothing, and counted, so a reader can fail
closed on it (the phase gates do).
Reporting only: nothing here changes a record, a setting, a limit or an order.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from goldbot.base import FrozenRecord, load_record
from goldbot.execution.costs import CONTRACT_OZ, CostTable, settings_swap
from goldbot.labels.triple_barrier import SwapSpec, rollover_nights

if TYPE_CHECKING:
    from goldbot.config import Settings
    from goldbot.ops.gates_phase import ClosedTrade


class AccountCostTerms(FrozenRecord):
    """What one account is charged: commission per lot per side (USD, >= 0) and the swap it pays (broker sign)."""

    commission_per_lot_side_usd: float
    swap: SwapSpec
    source: str                        # "cost table <account>" or "settings prior"


class TradeCharge(FrozenRecord):
    """The imputed cost of one closed record; all zero for a row whose return is already net."""

    imputed: bool = False              # the row's return is not net (a gross row), whether or not it could be priced
    priced: bool = True                # False: the charge could not be computed (counted, charged nothing)
    usd_per_oz: float = 0.0
    ret: float = 0.0                   # subtract from `ret`
    r: float | None = 0.0              # subtract from `r`; None when the initial risk is unknown
    usd: float | None = 0.0            # money charged (USD), None when the lots are unknown


class ImputationSummary(FrozenRecord):
    """How many rows of a set were charged and how much, for the evidence text a reader shows."""

    n: int = 0
    n_gross: int = 0
    n_unpriced: int = 0
    total_ret: float = 0.0
    total_usd: float = 0.0

    def text(self) -> str:
        if not self.n_gross:
            return "all net"
        out = f"{self.n_gross} gross row(s) charged an imputed cost of {self.total_ret:.5f} in return (~${self.total_usd:,.2f})"
        if self.n_unpriced:
            out += f"; {self.n_unpriced} could not be priced (no price or entry time)"
        return out


def _account_of(path: Path) -> str:
    return path.stem[len("costs_"):]


class CostImputer:
    """Charges gross rows (see the module docstring). Build it once per evaluation with `from_state`."""

    def __init__(self, terms: dict[str, AccountCostTerms], fallback: dict[str, AccountCostTerms],
                 default: AccountCostTerms):
        self.terms = terms             # by account id (the cost tables)
        self.fallback = fallback       # by broker name (the settings priors)
        self.default = default         # an unknown broker: the largest commission configured, the canonical clock

    @classmethod
    def from_settings(cls, settings: Settings) -> CostImputer:
        """Priors only (no cost tables): the settings commission per broker and the swap prior."""
        prior = settings_swap(settings)
        commissions = settings.costs.commission_per_lot_side_usd
        fallback = {b: AccountCostTerms(commission_per_lot_side_usd=max(commissions.get(b, 0.0), 0.0),
                                        swap=prior.model_copy(update={"server_tz": cfg.server_tz}),
                                        source="settings prior")
                    for b, cfg in settings.brokers.items()}
        default = AccountCostTerms(commission_per_lot_side_usd=max([0.0, *commissions.values()]), swap=prior,
                                   source="settings prior")
        return cls({}, fallback, default)

    @classmethod
    def from_state(cls, state_dir: Path, settings: Settings) -> CostImputer:
        """The priors, overridden per account by its readable nightly cost table (an unreadable one keeps the prior).
        The table's server clock is its account's broker's (unknown account: the canonical broker's)."""
        base = cls.from_settings(settings)
        terms: dict[str, AccountCostTerms] = {}
        for f in sorted(Path(state_dir).glob("costs_*.json")):
            try:
                table = load_record(CostTable, json.loads(f.read_text()), source="cost_table")
            except (ValueError, OSError):
                continue
            account = table.account_id or _account_of(f)
            prior = base.default.swap
            swap = table.swap_spec(prior.server_tz, prior.triple_weekday) or prior
            terms[account] = AccountCostTerms(commission_per_lot_side_usd=max(table.commission_per_lot_side_usd, 0.0),
                                              swap=swap, source=f"cost table {account}")
        return cls(terms, base.fallback, base.default)

    def terms_for(self, t: ClosedTrade) -> AccountCostTerms:
        found = self.terms.get(t.account_id)
        broker = self.fallback.get(t.broker)
        if found is not None:          # the table's rates on the trade's own broker clock
            return found if broker is None else found.model_copy(
                update={"swap": found.swap.model_copy(update={"server_tz": broker.swap.server_tz})})
        return broker or self.default

    @staticmethod
    def price_return(t: ClosedTrade) -> bool:
        """`ret` is the gross price return: marked "price", or (review R1) a record from before `ret_basis` or from a
        rolled-back release that the broker-deals path with no conversion wrote, recognisable by its money from the
        broker, its initial stop known and its R left empty (that path never had a conversion for R either)."""
        if t.ret_basis is not None:
            return t.ret_basis == "price"
        return t.money_source == "broker" and t.r is None and t.initial_sl is not None

    def charge(self, t: ClosedTrade) -> TradeCharge:
        """The imputed cost of `t`: the components its return does not carry (a price return carries neither)."""
        from_price = self.price_return(t)
        need_comm = from_price or t.commission is None
        need_swap = from_price or t.swap is None
        if not (need_comm or need_swap):
            return TradeCharge()
        price = t.entry_price if t.entry_price is not None and t.entry_price > 0 else t.exit_price
        if price is None or price <= 0 or (need_swap and t.entry_utc is None):
            return TradeCharge(imputed=True, priced=False, r=None, usd=None)
        terms = self.terms_for(t)
        per_oz = 2 * terms.commission_per_lot_side_usd / CONTRACT_OZ if need_comm else 0.0
        if need_swap and t.entry_utc is not None:
            nights = int(rollover_nights(pd.DatetimeIndex([t.entry_utc]), pd.DatetimeIndex([t.exit_utc]),
                                         terms.swap.server_tz, terms.swap.triple_weekday)[0])
            rates = {1: [terms.swap.long_usd_per_lot], -1: [terms.swap.short_usd_per_lot]}.get(
                t.side or 0, [terms.swap.long_usd_per_lot, terms.swap.short_usd_per_lot])   # unknown side: the larger
            per_oz += nights * max(0.0, *(-x for x in rates)) / terms.swap.contract_oz
        if t.entry_price is not None and t.initial_sl is not None and abs(t.entry_price - t.initial_sl) > 0:
            r: float | None = per_oz / abs(t.entry_price - t.initial_sl)
        elif t.r is not None and t.ret and not from_price:
            r = per_oz / price * (t.r / t.ret)        # r / ret = notional / initial risk (same P&L, same currency)
        else:
            r = None
        usd = per_oz * t.lots * CONTRACT_OZ if t.lots is not None else None
        return TradeCharge(imputed=True, usd_per_oz=per_oz, ret=per_oz / price, r=r, usd=usd)

    def net_ret(self, t: ClosedTrade) -> float:
        return t.ret - self.charge(t).ret

    def net_rets(self, trades: list[ClosedTrade]) -> tuple[list[float], ImputationSummary]:
        """Each trade's return less its imputed cost (gross rows kept, never dropped), and the summary."""
        rets: list[float] = []
        n_gross = n_unpriced = 0
        total_ret = total_usd = 0.0
        for t in trades:
            c = self.charge(t)
            rets.append(t.ret - c.ret)
            if c.imputed:
                n_gross += 1
                n_unpriced += 0 if c.priced else 1
                total_ret += c.ret
                total_usd += c.usd or 0.0
        return rets, ImputationSummary(n=len(trades), n_gross=n_gross, n_unpriced=n_unpriced, total_ret=total_ret,
                                       total_usd=total_usd)
