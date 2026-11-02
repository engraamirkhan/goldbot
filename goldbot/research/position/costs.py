"""C1 point costs in basis points for the position engine.

C1: "RT_point_bps, OS_point_bps: the cost of record's ICM POINT estimates (not the upper bound), converted at reference
price 4,000." W3.2: "costs_d = RT_point_bps / 2 x |delta e| on each rebalance. Disaster-stop exits also pay the C1
overshoot."

SWAP LATER: lane research/c1-rescore adds the shared C1 bps cost function; it had not merged when this engine was
written, so `point_bps_of_record` is the one local stand-in. When it lands, replace its body with that function and
keep `PointCosts` as the engine's input record.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field

from goldbot.base import FrozenRecord, load_record
from goldbot.config import PositionSettings
from goldbot.research.arms import CostOfRecord

COST_OF_RECORD = Path(__file__).resolve().parents[3] / "config" / "cost_of_record.yaml"


class PointCosts(FrozenRecord):
    """One market's C1 point costs in bps of price: the full round trip and the stop-exit overshoot."""
    rt_bps: float = Field(ge=0)
    overshoot_bps: float = Field(ge=0)


def point_bps_of_record(path: Path | str = COST_OF_RECORD, *, broker: str = "icm", session: str = "all",
                        reference_price: float = 4000.0) -> PointCosts:
    """The cost of record's point round trip and overshoot (USD per oz) for `broker` x `session`, over the reference."""
    table = load_record(CostOfRecord, yaml.safe_load(Path(path).read_text()), source="cost_of_record")
    cells = table.cells[broker]                       # ADR 0043: never another broker's cell (KeyError)
    cell = cells.get(session, cells["all"])
    return PointCosts(rt_bps=cell.round_trip_usd / reference_price * 1e4,
                      overshoot_bps=max(0.0, cell.overshoot_usd) / reference_price * 1e4)


def costs_for(symbol: str, s: PositionSettings, path: Path | str = COST_OF_RECORD) -> PointCosts:
    """XAUUSD from the cost of record; any other market from `round_trip_bps_by_symbol` (overshoot defaults to its
    half round trip). A market with no cost is refused: it is never priced at gold's cost."""
    if symbol == "XAUUSD":
        return point_bps_of_record(path, broker=s.cost_broker, session=s.cost_session,
                                   reference_price=s.cost_reference_price)
    if symbol not in s.round_trip_bps_by_symbol:
        raise KeyError(f"no point cost for {symbol}: set research.position.round_trip_bps_by_symbol")
    rt = s.round_trip_bps_by_symbol[symbol]
    return PointCosts(rt_bps=rt, overshoot_bps=s.overshoot_bps_by_symbol.get(symbol, rt / 2))
