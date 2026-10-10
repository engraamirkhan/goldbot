from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    import pandas as pd

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
    # overnight financing per lot (100 oz) per night, broker sign (negative = paid): a prior until the canonical
    # broker's cost table reports its own (CostTable.swap_*); charged per server-day rollover, x3 on the triple day
    swap_long_usd_per_lot: float = -60.0
    swap_short_usd_per_lot: float = 0.0
    swap_triple_weekday: int = Field(2, ge=0, le=4)      # 0 = Monday; Wednesday for XAUUSD at most brokers
    # nightly_costs publishes the canonical broker's table (costs only) to release costs-v1 for research.yml;
    # needs the github-token in the keyring. Health warns when the published table is older than 8 days.
    publish_release: bool = False


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
    model_watch: ScheduleSettings
    tournament: ScheduleSettings
    research_director: ScheduleSettings
    agents_daily: ScheduleSettings
    agents_weekly: ScheduleSettings
    monthly_research: ScheduleSettings
    calendar_archive: ScheduleSettings
    agents_presession: ScheduleSettings
    recalibrate: ScheduleSettings
    drift_watch: ScheduleSettings
    feed_reconcile: ScheduleSettings
    gap_watch: ScheduleSettings = ScheduleSettings(kind="daily", at="23:55", max_late_hours=20)
    attribution: ScheduleSettings = ScheduleSettings(kind="daily", at="23:50", max_late_hours=20)


class GapSettings(_Section):
    """Bounded spawning (goldbot/ops/gap_watch.py, docs/TRADER_LIFECYCLE.md section 3)."""
    founders_per_month: int = Field(2, ge=0, le=4)        # zero-capital shadow founders per calendar month
    staff_runs_per_week: int = Field(3, ge=0, le=7)       # on-demand runs of existing read-only roles, rolling 7 days
    hypotheses_per_run: int = Field(2, ge=0, le=2)        # hypotheses gap_watch may file per run
    dq_window_days: int = Field(7, ge=1, le=30)           # data-quality errors counted over this window
    dq_min_error_days: int = Field(3, ge=1, le=30)        # distinct UTC days with error events that make a gap
    regime_window_days: int = Field(20, ge=5, le=120)     # recent realised volatility (mean of daily values)
    regime_history_days: int = Field(3650, ge=365)        # history the volatility terciles are cut from


class AttributionSettings(_Section):
    """Daily performance attribution (goldbot/research/attribution.py, BACKLOG item 12). Reporting only: it changes
    no trading, setting or model; the staff agents read it and file hypotheses through the bounded path."""
    window_days: int = Field(180, ge=7, le=3650)          # closed shadow trades exited in this window
    min_trades: int = Field(30, ge=2)                     # a cell with fewer trades is reported as noise
    calibration_bins: int = Field(10, ge=2, le=50)        # equal-width bins of p
    calibration_min_bin: int = Field(10, ge=1)            # a calibration bin with fewer candidates is noise
    trade_rows: int = Field(300, ge=0, le=5000)           # most recent per-trade cost rows kept in the JSON


class ResearchSettings(_Section):
    trial_budget_per_month: int = Field(12, ge=1, le=200)
    label_grid_paused: bool = True        # the monthly label-grid loop runs only when this is false (proposal P2)
    trial_budget_quarter: int = Field(20, ge=1, le=500)   # pre-registered trials per calendar quarter, all families
    holdout_from: date | None = date(2025, 10, 1)          # research never sees this window unless scoring it
    holdout_to: date | None = date(2026, 9, 30)            # inclusive
    director_floor: int = Field(2, ge=0, le=200)        # research director: exploration trials per family per month
    label_grid_step: float = Field(0.25, gt=0, lt=1)
    cost_window_days: int = Field(30, ge=1)
    fills_window_days: int = Field(180, ge=1)
    min_fills_for_slippage: int = Field(50, ge=1)
    # weekly bounded recalibration (proposal P9): only the probability map, on counterfactual shadow outcomes
    recal_min_samples: int = Field(50, ge=1)          # fewer recent outcomes: no update
    recal_prior_trades: float = Field(200.0, gt=0)    # the current calibration counts as this many trades
    recal_max_shift: float = Field(0.05, gt=0, le=0.2)   # most a probability may move in one run
    recal_window_days: int = Field(182, ge=7)         # outcomes exited within this many days
    registry: str = "state/research_registry.jsonl"
    models_dir: str = "models"

    def holdout_window(self) -> tuple[pd.Timestamp, pd.Timestamp] | None:
        """[start, end) in UTC, or None when no holdout is configured."""
        import pandas as pd
        if self.holdout_from is None or self.holdout_to is None:
            return None
        return (pd.Timestamp(self.holdout_from, tz="UTC"), pd.Timestamp(self.holdout_to, tz="UTC") + pd.Timedelta(days=1))


class AgentSettings(_Section):
    monthly_cap_usd: float = Field(40.0, ge=0)
    model: str = "claude-opus-5-5"


class NewsSettings(_Section):
    feeds: dict[str, str] = Field(default_factory=dict)       # name -> RSS/Atom URL
    poll_seconds: int = Field(300, ge=60)
    model: str = "claude-opus-5-5"
    daily_cap_usd: float = Field(0.50, ge=0)                  # scoring spend per UTC day (inside the agents' monthly cap)
    shock_blackout_min: int = Field(30, ge=0)
    shock_min_relevance: float = Field(0.7, ge=0, le=1)


class TelegramSettings(_Section):
    allowed_user_ids: list[int] = Field(default_factory=list)
    # design (Operating mode, row A10): /mode auto is offered only after this many decided proposals since the last
    # mode change, no RiskGate breach, and no distinguishable approved-vs-rejected outcome difference (Welch at alpha)
    auto_min_proposals: int = Field(100, ge=100)
    auto_alpha: float = Field(0.10, gt=0, le=0.5)
    auto_min_outcomes_per_side: int = Field(10, ge=2)   # fewer matched outcomes on a side: no evidence (fail closed)


class AuthSettings(_Section):
    """Dashboard owner. The repo is public: set owner_email only in the server's git-ignored
    config/settings.local.yaml. Unset, the owner account cannot be created (bootstrap refuses)."""
    owner_email: str | None = None


class DriftSettings(_Section):
    """Design: Drift and health (goldbot/research/drift.py)."""
    psi_warn: float = Field(0.1, gt=0)               # PSI on a top feature that warns
    psi_size_down: float = Field(0.25, gt=0)         # PSI on a top feature that sizes the agent down
    top_features: int = Field(10, ge=1, le=40)       # by gain importance (TreeSHAP rank once stored)
    ece_size_down: float = Field(0.08, gt=0)         # calibration error on the trailing trades that sizes down
    calib_trades: int = Field(100, ge=10)            # trailing taken trades for ECE / Brier
    min_calib_trades: int = Field(30, ge=10)         # fewer: calibration not judged
    size_down_factor: float = Field(0.5, gt=0, le=1)
    window_days: int = Field(30, ge=7)               # recent candidates for PSI
    min_rows: int = Field(50, ge=10)                 # fewer recent candidates: PSI not computed
    cusum_k: float = Field(0.5, ge=0)
    cusum_h: float = Field(4.0, gt=0)
    dd_mult: float = Field(1.5, gt=1)                # 30-day drawdown above this x backtest halts the system
    dd_window_days: int = Field(30, ge=7)


class GateSettings(_Section):
    """Design: Roadmap gates and the stop rule (goldbot/ops/gates_phase.py). Values without a comment are the design's
    own numbers; the rest are proposals the owner must sign off before the first paper trade."""
    shuffle_auc_tolerance: float = Field(0.02, gt=0, lt=0.5)      # PROPOSED: owner sign-off required ("at chance")
    feed_max_mismatch_share: float = Field(0.01, ge=0, le=1)      # PROPOSED: owner sign-off required ("bars agree")
    backtest_min_dsr: float = Field(0.95, gt=0, le=1)             # PROPOSED: owner sign-off required (roadmap 1.0 vs 0.95)
    backtest_min_trades: int = Field(500, ge=1)
    paper_min_trades: int = Field(150, ge=1)
    paper_min_days: int = Field(182, ge=0)                        # PROPOSED: owner sign-off required (roadmap "6 months")
    paper_expectancy_within: float = Field(0.5, gt=0, le=1)
    paper_fills_within: float = Field(0.3, gt=0)
    live_min_trades: int = Field(300, ge=1)
    live_min_days: int = Field(365, ge=0)                         # PROPOSED: owner sign-off required (roadmap "12 months")
    live_expectancy_within: float = Field(0.4, gt=0, le=1)
    live_dd_mult: float = Field(1.5, gt=0)
    brokers_within: float = Field(0.15, gt=0, le=1)
    broker_min_trades: int = Field(50, ge=1)                      # PROPOSED: owner sign-off required
    stop_after_months: float = Field(18, gt=0)
    stop_min_pooled_trades: int = Field(500, ge=1)
    stop_confidence: float = Field(0.90, gt=0.5, lt=1)


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
    agents: AgentSettings = Field(default_factory=AgentSettings)
    news: NewsSettings = Field(default_factory=NewsSettings)
    drift: DriftSettings = Field(default_factory=DriftSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    gates: GateSettings = Field(default_factory=GateSettings)
    gaps: GapSettings = Field(default_factory=GapSettings)
    attribution: AttributionSettings = Field(default_factory=AttributionSettings)


class _UniqueKeyLoader(yaml.SafeLoader):
    """safe_load that rejects duplicate keys (plain YAML silently keeps the last one)."""


def _no_duplicates(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)


def load_yaml(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.load(fh, Loader=_UniqueKeyLoader)   # a SafeLoader subclass: no arbitrary objects


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def settings_dict(path: str | Path = DEFAULT_SETTINGS) -> dict:
    """settings.yaml with the machine's own overrides merged on top: `settings.local.yaml` next to it (git-ignored),
    for values that belong to one server and never to the public repo (Telegram user ids), so automatic deploys
    (`git` fast-forward) never conflict with a local edit."""
    raw = load_yaml(path)
    local = Path(path).with_name("settings.local.yaml")
    return _deep_merge(raw, load_yaml(local) or {}) if local.exists() else raw


@lru_cache(maxsize=4)
def load_settings(path: str | Path = DEFAULT_SETTINGS) -> Settings:
    return Settings.model_validate(settings_dict(path))


def tf_seconds(tf: str) -> int:
    try:
        return TF_SECONDS[tf]
    except KeyError as exc:
        raise ValueError(f"unknown timeframe {tf!r}; known: {sorted(TF_SECONDS)}") from exc
