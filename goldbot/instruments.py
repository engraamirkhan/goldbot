"""Instrument registry: one `InstrumentSpec` per tradeable instrument, read from config/instruments.yaml.

Multi-instrument demo plan (architect, adopted 2026-11-02, step 0): the interfaces are frozen here first so the
four lanes (L1 instruments+sizing, L2 risk, L3 engine, L4 bridge/ops/UI) code against the same shape. Design rules
this file carries, quoted from the plan:
- "magic = 3_000_000 + account_idx*10_000 + magic_block*100 + offset (magic_block per instrument in config)": every
  instrument has a unique magic_block in [0, 100), so blocks never collide inside one account's 10_000 range.
  XAUUSD is block 0.
- "store bars_1m_live keyed (source=account, symbol=store_symbol), never pooled with XAUUSD": store_symbol is unique.
- "leverage per asset class (FX 30, gold/major idx 20, other commodities 10, bonds 5)": LEVERAGE_BY_CLASS.
- "notional per correlation group (gold, metals, equity, FX, energy, rates, ags)": correlation_group.
- "XAUUSD paths byte-identical": nothing reads this registry yet; the non-gold entries load with `enabled: false`
  and their terms are placeholders marked `verify: true` until the symbol check (L1) fills them from the terminal.
"""
from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from goldbot.base import FrozenRecord
from goldbot.config import ROOT, load_yaml

DEFAULT_INSTRUMENTS = ROOT / "config" / "instruments.yaml"
GOLD = "XAUUSD"         # the key of today's instrument: every path keyed by it stays byte-identical

# Plan: "magic = 3_000_000 + account_idx*10_000 + magic_block*100 + offset". XAUUSD keeps each account's own
# magic_base (accounts.yaml), so every gold position reconciles as before; the other instruments of an account live
# in its own 10_000 range above every magic_base (`goldbot.ops.accounts` refuses an overlap at load)
MAGIC_INSTRUMENT_BASE = 3_000_000
MAGIC_ACCOUNT_STEP = 10_000
MAGIC_BLOCK_STEP = 100

AssetClass = Literal["metal", "index", "fx", "energy", "bond", "agri"]
CorrelationGroup = Literal["gold", "metals", "equity", "fx", "energy", "rates", "ags"]
LeverageClass = Literal["fx", "gold_index", "commodity", "bond"]
# the broker's trading-hours family; L2 checks each against the broker's own trading_sessions (TRACEABILITY D13)
SessionProfile = Literal["metals", "fx", "index_us", "index_eu", "index_uk", "index_jp", "energy", "rates_us",
                         "rates_eu", "ags", "softs"]

# Plan: "leverage per asset class (FX 30, gold/major idx 20, other commodities 10, bonds 5)" (ESMA retail caps; bonds
# held to 5 by the plan). The RiskGate's margin check takes the larger of the broker's figure and this one (L2)
LEVERAGE_BY_CLASS: dict[str, int] = {"fx": 30, "gold_index": 20, "commodity": 10, "bond": 5}


class InstrumentSpec(FrozenRecord):
    key: str                                      # broker-neutral name, the entry's key in instruments.yaml
    broker_symbols: dict[str, str]                # accounts.yaml `broker` -> that broker's symbol name
    store_symbol: str                             # the store's symbol column (bars_1m_live, ticks); never shared
    asset_class: AssetClass
    correlation_group: CorrelationGroup
    magic_block: int = Field(ge=0, lt=100)
    contract_size: float = Field(gt=0)            # units per lot (oz, barrels, index points x 1, base currency ...)
    digits: int = Field(ge=0)
    point: float = Field(gt=0)
    min_lot: float = Field(gt=0)
    lot_step: float = Field(gt=0)
    currency_profit: str = Field(min_length=3, max_length=3)    # P&L currency (MT5 SYMBOL_CURRENCY_PROFIT)
    swap_rollover3days: int = Field(ge=0, le=6)   # triple-swap weekday, MT5 numbering: 0 = Sunday .. 6 = Saturday
    session_profile: SessionProfile
    news_currencies: tuple[str, ...]              # news blackouts by these currencies (L2 adds EIA / WASDE by group)
    leverage_class: LeverageClass
    enabled: bool = False
    verify: bool = True                           # True = terms are placeholders until the symbol check confirms them


class Instruments(FrozenRecord):
    instruments: dict[str, InstrumentSpec]

    @model_validator(mode="after")
    def _consistent(self) -> Instruments:
        for name, spec in self.instruments.items():
            if spec.key != name:
                raise ValueError(f"instrument {name!r}: key {spec.key!r} must equal its entry name")
        blocks: dict[int, str] = {}
        stores: dict[str, str] = {}
        symbols: dict[tuple[str, str], str] = {}
        for name, spec in self.instruments.items():
            if spec.magic_block in blocks:
                raise ValueError(f"magic_block {spec.magic_block} used by {blocks[spec.magic_block]!r} and {name!r}")
            blocks[spec.magic_block] = name
            if spec.store_symbol in stores:
                raise ValueError(f"store_symbol {spec.store_symbol!r} used by {stores[spec.store_symbol]!r} and "
                                 f"{name!r}")
            stores[spec.store_symbol] = name
            for broker, sym in spec.broker_symbols.items():
                if (broker, sym) in symbols:
                    raise ValueError(f"broker symbol {sym!r} on {broker!r} mapped by {symbols[(broker, sym)]!r} "
                                     f"and {name!r}")
                symbols[(broker, sym)] = name
        return self

    def get(self, key: str) -> InstrumentSpec:
        return self.instruments[key]

    def for_broker(self, broker: str) -> list[InstrumentSpec]:
        """Every instrument this broker lists (enabled or not), in file order."""
        return [s for s in self.instruments.values() if broker in s.broker_symbols]

    def by_broker_symbol(self, broker: str, symbol: str) -> InstrumentSpec | None:
        return next((s for s in self.instruments.values() if s.broker_symbols.get(broker) == symbol), None)

    def resolve(self, name: str, broker: str | None = None) -> InstrumentSpec | None:
        """The instrument `name` stands for: its key, else its store symbol, else a broker's symbol for it (that
        broker's only, when given: "COPPER-C" is COPPER on vantage and nothing on icm). None when unknown: a caller
        never guesses terms for a symbol the registry does not list."""
        if name in self.instruments:
            spec = self.instruments[name]
            return spec if broker is None or broker in spec.broker_symbols else None
        for spec in self.instruments.values():
            if spec.store_symbol == name and (broker is None or broker in spec.broker_symbols):
                return spec
        for spec in self.instruments.values():
            if (spec.broker_symbols.get(broker) == name if broker is not None
                    else name in spec.broker_symbols.values()):
                return spec
        return None


def load_instruments(path: str | Path = DEFAULT_INSTRUMENTS) -> Instruments:
    """Strict: an unknown or misspelt key, a duplicate yaml key, a repeated magic_block, store_symbol or broker
    symbol fails at load (fail closed)."""
    return Instruments.model_validate(load_yaml(path))


@lru_cache(maxsize=1)
def registry() -> Instruments:
    """config/instruments.yaml, read once per process (a service restarts to pick up an edit, as for accounts.yaml)."""
    return load_instruments()


def spec_of(name: str, broker: str | None = None) -> InstrumentSpec:
    """`Instruments.resolve` on the registry, KeyError when unknown (fail closed: never a gold default)."""
    spec = registry().resolve(name, broker)
    if spec is None:
        raise KeyError(f"{name!r} is not an instrument in config/instruments.yaml"
                       + (f" for broker {broker!r}" if broker else ""))
    return spec


def contract_size(name: str) -> float:
    """Units per lot of the instrument (XAUUSD: 100 oz). Replaces the old CONTRACT_OZ constant: one source, the file."""
    return spec_of(name).contract_size


def point_of(name: str) -> float:
    """The instrument's point, the unit `spread_points` and `stops_level_points` count in (XAUUSD: 0.01)."""
    return spec_of(name).point


def arm_instrument(params: Mapping[str, Any]) -> str:
    """The instrument an exploration arm trades: its `params.instrument` (config/exploration_arms.yaml; hashed, so an
    arm on another instrument is another arm id), else XAUUSD (every arm before the multi-instrument plan)."""
    v = params.get("instrument")
    return str(v) if v else GOLD


def instrument_magic_base(account_index: int, spec: InstrumentSpec) -> int:
    """The first magic of `spec` on the account with this `instrument_magic_index` (plan formula, offset 0); the
    instrument's own orders take this + the usual offsets (goldbot.engine.magic: families crc32 % 100, 95 .. 99)."""
    return MAGIC_INSTRUMENT_BASE + account_index * MAGIC_ACCOUNT_STEP + spec.magic_block * MAGIC_BLOCK_STEP


def triple_weekday(spec: InstrumentSpec) -> int:
    """The triple-swap weekday in the cost model's numbering (0 = Monday, `SwapSpec.triple_weekday`), from the spec's
    MT5 numbering (0 = Sunday). A weekend day is refused: no rollover is charged then, so it is a misread spec."""
    day = (spec.swap_rollover3days - 1) % 7
    if day > 4:
        raise ValueError(f"{spec.key}: swap_rollover3days {spec.swap_rollover3days} is not a weekday (MT5 0 = Sunday)")
    return day
