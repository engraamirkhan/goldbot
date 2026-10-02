from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS = ROOT / "config" / "settings.yaml"

TF_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1w": 7 * 86400,
}


Timeframe = Literal["1m", "5m", "15m", "1h", "4h", "1d", "1w"]
DecisionTimeframe = Literal["15m", "1h"]


class _Section(BaseModel):
    """settings.yaml is validated strictly: an unknown or misspelt key fails at load, not at 3 a.m."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DayAnchor(_Section):
    tz: str
    time: str = Field(pattern=r"^\d{2}:\d{2}$")


class Timeframes(_Section):
    decision: list[DecisionTimeframe]
    context: list[Timeframe]


class BrokerSettings(_Section):
    server_tz: str
    symbol_map: dict[str, str]
    magic_base: int
    canonical_costs: bool = False


class FeatureSettings(_Section):
    max_live_features: int = Field(40, ge=1, le=40)   # design cap: never more than 40 live features
    version: str


class LabelSettings(_Section):
    embargo_days: dict[DecisionTimeframe, int]
    purge_days: dict[DecisionTimeframe, int]


class WalkForwardWindow(_Section):
    train_months: int = Field(gt=0)
    test_months: int = Field(gt=0)
    step_months: int = Field(gt=0)


class Blackout(_Section):
    before_min: int = Field(ge=0)
    after_min: int = Field(ge=0)
    events: list[str]


class RiskSettings(_Section):
    risk_per_trade: float = Field(gt=0, le=0.01)
    risk_per_trade_tiny_live: float = Field(gt=0, le=0.01)
    multiplier_bounds: tuple[float, float]
    daily_cap: float = Field(gt=0, lt=1)
    weekly_cap: float = Field(gt=0, lt=1)
    supervisor_daily_cap: float = Field(gt=0, lt=1)
    supervisor_weekly_cap: float = Field(gt=0, lt=1)
    drawdown_stage1: float = Field(gt=0, lt=1)
    drawdown_stage2: float = Field(gt=0, lt=1)
    drawdown_stage1_clear: float = Field(gt=0, lt=1)
    max_positions_per_account: int = Field(ge=1)
    max_spread_points: float = Field(gt=0)
    stale_tick_seconds: int = Field(gt=0)
    blackout: Blackout
    min_target_over_cost: float = Field(gt=0)
    approval_window_seconds: int = Field(gt=0)


class CostSettings(_Section):
    slippage_prior_usd: float = Field(ge=0)
    commission_per_lot_side_usd: dict[str, float]


class ScheduleSettings(_Section):
    kind: Literal["daily", "weekly", "monthly"]
    at: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    weekday: int | None = Field(None, ge=0, le=6)
    day: int | None = Field(None, ge=1, le=28)
    max_late_hours: float = Field(12.0, gt=0)


class SchedulerSettings(_Section):
    nightly_costs: ScheduleSettings
    saturday_retrain: ScheduleSettings
    monthly_research: ScheduleSettings


class ResearchSettings(_Section):
    trial_budget_per_month: int = Field(12, ge=1, le=200)
    label_grid_step: float = Field(0.25, gt=0, lt=1)
    cost_window_days: int = Field(30, ge=1)
    fills_window_days: int = Field(180, ge=1)
    min_fills_for_slippage: int = Field(50, ge=1)
    registry: str = "state/research_registry.jsonl"
    models_dir: str = "models"


class TelegramSettings(_Section):
    allowed_user_ids: list[int] = Field(default_factory=list)


class Settings(_Section):
    symbol: str
    data_root: str
    feature_day_anchor: DayAnchor
    risk_day_anchor: DayAnchor
    timeframes: Timeframes
    brokers: dict[str, BrokerSettings]
    features: FeatureSettings
    labels: LabelSettings
    walkforward: dict[DecisionTimeframe, WalkForwardWindow]
    risk: RiskSettings
    costs: CostSettings
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    scheduler: SchedulerSettings
    research: ResearchSettings = Field(default_factory=ResearchSettings)


@lru_cache(maxsize=4)
def load_settings(path: str | Path = DEFAULT_SETTINGS) -> Settings:
    with open(path, "r", encoding="utf-8") as fh:
        return Settings.model_validate(yaml.safe_load(fh))


def tf_seconds(tf: str) -> int:
    try:
        return TF_SECONDS[tf]
    except KeyError as exc:
        raise ValueError(f"unknown timeframe {tf!r}; known: {sorted(TF_SECONDS)}") from exc
