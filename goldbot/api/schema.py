"""API contract (Pydantic models) shared by the backend and the generated TypeScript client.
Phase 1 fills the routers; the shapes are fixed here so the frontend can start in parallel."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class AccountSummary(BaseModel):
    account_id: str
    broker: str
    mode: Literal["demo", "live", "paper"]
    equity: float
    day_pnl_pct: float
    week_pnl_pct: float
    drawdown_pct: float
    stage: Literal["normal", "size_down", "halted"]
    open_positions: int
    account_class: Literal["raw", "standard", "unknown"]


class Proposal(BaseModel):
    proposal_id: str
    account_id: str
    agent_id: str
    side: Literal["long", "short"]
    lots: float
    entry: float
    stop: float
    target: float
    p: float
    ev_r: float
    spread_points: float
    top_features: list[tuple[str, float]]
    expires_at: datetime
    tradingview_url: str | None = None


class Decision(BaseModel):
    proposal_id: str
    action: Literal["approve", "reject"]
    reason_code: Literal["news", "cost", "discretion", "duplicate", "other"] | None = None


class AgentRow(BaseModel):
    agent_id: str
    family: str
    generation: int
    parent_id: str | None
    status: Literal["shadow", "live", "retired"]
    n_trades: int
    expectancy_r: float
    hit_rate: float
    calibration_ece: float
    fitness: float
    capital_weight: float


class FeedHealth(BaseModel):
    account_id: str
    last_tick_age_s: float
    spread_points: float
    terminal_connected: bool
    webhook_p99_latency_s: float | None
    supervisor_heartbeat_age_s: float
