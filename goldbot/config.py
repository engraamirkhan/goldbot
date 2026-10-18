from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

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
# Timeframes an agent may decide on live: each needs a walk-forward window (the Saturday retrain) and the engine must
# hold enough 1m history for 120 of its bars. 1d is research-only: the engine keeps ~40 trading days of 1m bars
# (EngineConfig.max_bars_in_memory), so a daily agent would never get a frame.
DecisionTimeframe = Literal["15m", "1h", "4h"]


class _Section(BaseModel):
    """settings.yaml is validated strictly: an unknown or misspelt key fails at load, not at 3 a.m."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DayAnchor(_Section):
    tz: str
    time: str = Field(pattern=r"^\d{2}:\d{2}$")


# The only values Settings accepts for each day anchor (BACKLOG 57): (tz, time, what the code does at that time).
_FIXED_DAY_ANCHORS: dict[str, tuple[str, str, str]] = {
    "risk_day_anchor": ("UTC", "00:00", "the loss caps reset at 00:00 UTC in code (timeutil.risk_day)"),
    "feature_day_anchor": ("America/New_York", "13:30",
                           "daily bars and features close at 13:30 New York in code (timeutil.feature_day)"),
}


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


class Tier2Blackout(_Section):
    """BACKLOG 18 (playbook G-3): "Tier-2 USD events (PPI, retail sales, ISM, GDP, JOLTS, claims, FOMC minutes, Fed
    chair) get a -5/+15 min entry blackout from config". From the live calendar (store table calendar_events, the
    archived Forex Factory feed, tiers from goldbot/data/econ_calendar.py). Entries only; with no calendar to judge by,
    entries are refused (`calendar_unavailable`). Off until the owner adopts it."""

    enabled: bool = False
    before_min: int = Field(5, ge=0)
    after_min: int = Field(15, ge=0)


class Blackout(_Section):
    before_min: int = Field(ge=0)
    after_min: int = Field(ge=0)
    events: list[str]
    tier2: Tier2Blackout = Field(default_factory=Tier2Blackout)


class ReopenSettings(_Section):
    """BACKLOG 17 (playbook G-2): "No entries for 15 min after each daily reopen and 30 min after the weekly open"
    (the minutes are the owner's to set). The reopen is read from the broker session table (D13; the static table
    otherwise), so it follows the server clock's DST. A reopen after a closure of 12 h or more (the weekend, a holiday
    closure) takes the weekly window. Entries only; off until the owner adopts it."""

    enabled: bool = False
    after_daily_reopen_min: int = Field(10, ge=0)
    after_monday_open_min: int = Field(30, ge=0)


class HolidaySettings(_Section):
    """BACKLOG 17 (playbook G-2): "Holiday calendar ...: no entries on those days". The days come from a versioned
    list in config (`file`, relative to the repo root), keyed by broker server date (midnight = 17:00 New York, the CME
    trading-day roll). A missing, unreadable or expired list refuses entries (`calendar_unavailable`). Entries only;
    off until the owner adopts it."""

    enabled: bool = False
    file: str = "config/holidays_cme_metals.yaml"


class ThrottleSettings(_Section):
    """BACKLOG 21 (playbook G-6), the system analogue of revenge trading and overtrading: per agent per account,
    after `loss_streak` consecutive full losses no entries until `cooldown_bars` bars of its timeframe pass or a real
    shadow win of its live model entered after the last loss (the count persists past the cooldown until such a win);
    at most `max_entries_per_day_per_agent` entries per risk day. Checked at every entry decision; never exits.
    Off until the owner sets the thresholds (risk-policy values: playbook section 7)."""

    enabled: bool = False
    loss_streak: int = Field(3, ge=1)
    cooldown_bars: int = Field(4, ge=1)
    max_entries_per_day_per_agent: int = Field(3, ge=1)


class HeatSettings(_Section):
    """BACKLOG 19 (playbook G-4: "sum of open risk ... <= the configured cap; same-side positions of different agents
    count as one bet"), per account: an entry is refused when the open positions' remaining stop risk plus its own
    exceeds `max_open_risk` x equity, or once `max_same_direction` positions on its side are open. Entries only; exits
    are never gated. Off until the owner sets the values (risk-policy values: playbook section 7)."""

    enabled: bool = False
    max_open_risk: float = Field(0.02, gt=0, lt=1)
    max_same_direction: int = Field(2, ge=1)


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
    # BACKLOG 25 (ADR 0004): when the sized lots fall below the broker's minimum lot, place exactly the minimum lot if
    # its realised risk (account currency, fraction of equity) is within this cap; half of it in the 8% size-down
    # stage. Never above the 1% per-trade maximum; never in tiny-live. null = off: the minimum lot only within 1.2x the
    # target (design). The absolute daily loss limit (BACKLOG 29) is per account: config/accounts.yaml
    min_lot_risk_cap: float | None = Field(None, gt=0, le=0.01)
    # health: an engine whose broker reads (positions(), deal history) failed this many times in a row FAILs and the
    # owner is alerted on Telegram; fewer is a warning (entries are blocked from the first failure, exits keep trying)
    positions_unreadable_alert: int = Field(5, ge=1)
    # BACKLOG 21: per-agent loss-streak and entry-rate throttle (goldbot/risk/throttle.py); off by default
    throttle: ThrottleSettings = Field(default_factory=ThrottleSettings)
    # BACKLOG 19: open-risk heat cap and same-direction cap (goldbot/risk/open_risk.py); off by default
    heat: HeatSettings = Field(default_factory=HeatSettings)
    # BACKLOG 35: with an absolute daily loss limit (accounts.yaml), the projection also counts the open positions'
    # remaining stop risk; off by default
    project_open_risk: bool = False
    # BACKLOG 44 (ADR 0016): a 12% drawdown read trips the kill switch at once when |equity - (balance + open P&L)| is
    # within this fraction of the high-water mark, else only when the next account read is also at 12%
    equity_check_tolerance: float = Field(0.02, gt=0, lt=0.12)
    # ...and a pending 12% read clears only after every read stayed below 12% minus the tolerance for this long
    equity_recovery_s: float = Field(60.0, gt=0, le=3600)
    # BACKLOG 54 (ADR 0018): a balance read above the high-water mark raises it only when the deal history since the
    # mark explains it, or the next read agrees with it, within this fraction of the mark (rounding, not money)
    hwm_rise_tolerance: float = Field(0.001, gt=0, lt=0.02)
    # BACKLOG 17: entry windows after the daily reopen / weekly open and on thin-liquidity days; off by default
    reopen: ReopenSettings = Field(default_factory=ReopenSettings)
    holidays: HolidaySettings = Field(default_factory=HolidaySettings)


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
    months: list[int] = Field(default_factory=lambda: list(range(1, 13)))   # e.g. [1, 4, 7, 10]: quarterly
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
    # encrypted off-host backup (goldbot/ops/backup.py) next to the daily break, before the nightly jobs start (no CPU
    # contention with them on the 4-core brain), and its weekly restore drill
    # on Sunday while the market is closed
    backup: ScheduleSettings = ScheduleSettings(kind="daily", at="22:15", max_late_hours=20)
    restore_drill: ScheduleSettings = ScheduleSettings(kind="weekly", at="10:00", weekday=6, max_late_hours=30)
    cpcv_quarterly: ScheduleSettings = ScheduleSettings(kind="monthly", at="14:00", weekday=6, months=[1, 4, 7, 10],
                                                        max_late_hours=30)
    attribution: ScheduleSettings = ScheduleSettings(kind="daily", at="23:50", max_late_hours=20)
    # broker ledger (goldbot/ops/ledger.py, finance audit GAP-07/GAP-08): balance snapshot, deposits and withdrawals,
    # positions and balance reconciled with the broker. Every day: the deal history still changes on weekends
    broker_ledger: ScheduleSettings = ScheduleSettings(kind="daily", at="23:25", max_late_hours=20)
    # review M2 (BACKLOG 18): a second calendar fetch on Sunday afternoon, so the new Forex Factory week is archived
    # before the weekly open (an enabled tier-2 window refuses entries until the archive covers the next 24 h)
    calendar_archive_sunday: ScheduleSettings = ScheduleSettings(kind="weekly", at="18:00", weekday=6, max_late_hours=3)
    # BACKLOG 79: the broker's ticks of each closed server day -> state/ticks/<account>/ (goldbot/data/ticks.py). Only
    # while gold is closed (trading-safety review): the daily break 17:00-18:00 New York is 21:00 UTC in US summer time
    # and 22:00 UTC in winter, so one slot per regime; the other one skips (`ticks.market_quiet`). Never caught up late
    # into London/NY: max_late_hours stays at most 1
    tick_record: ScheduleSettings = ScheduleSettings(kind="daily", at="21:10", max_late_hours=0.5)
    tick_record_winter: ScheduleSettings = ScheduleSettings(kind="daily", at="22:10", max_late_hours=0.5)


class BackupSettings(_Section):
    """restic to Oracle Object Storage (goldbot/ops/backup.py; credentials only in the secret store)."""
    host: str = "goldbot-brain"                   # restic --host: stable across a rebuilt VM, so retention keeps working
    work_dir: str = "~/.cache/goldbot/backup"     # staging and drill scratch, same filesystem as data_root
    # proposal section 7: --keep-daily 14 --keep-weekly 8 --keep-monthly 12, applied from the owner's Mac by
    # scripts/backup_retention.sh (never by the brain; tests keep the script's defaults equal to these)
    keep_daily: int = Field(14, ge=1)
    keep_weekly: int = Field(8, ge=0)
    keep_monthly: int = Field(12, ge=0)
    data_exclude_sources: list[str] = Field(default_factory=lambda: ["dukascopy", "fred"])   # on releases
    data_exclude_tables: list[str] = Field(default_factory=lambda: ["features", "labels"])   # derived
    check_subset: str = Field("5%", pattern=r"^\d{1,2}(\.\d+)?%$")   # restore drill: restic check --read-data-subset
    timeout_s: int = Field(3600, ge=60)
    # Object Storage size, previous versions included (health: object_storage; the free tier is 20 GB, BACKLOG 67)
    storage_warn_gb: float = Field(15.0, gt=0)
    storage_fail_gb: float = Field(19.0, gt=0)
    storage_max_age_h: float = Field(48.0, gt=0)      # an older reading counts as unknown (warn)


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


class LedgerSettings(_Section):
    """Broker ledger (goldbot/ops/ledger.py; finance audit GAP-07, GAP-08, checks RC-8 and RC-9). Read-only: it never
    places, changes or closes an order. RC-9: "Opening balance + net closed P&L + deposits - withdrawals = closing
    balance"; a daily difference beyond `balance_tolerance` + `balance_tolerance_per_deal` x deals FAILs the health
    check reconcile:<account>, a trade that differs from the broker's deals WARNs."""
    balance_tolerance: float = Field(0.05, ge=0)          # account currency, per day: rounding, not money
    balance_tolerance_per_deal: float = Field(0.01, ge=0) # plus this per deal in the day (audit RC-9: 0.01 per trade)
    price_tolerance: float = Field(0.01, ge=0)            # $/oz (one XAUUSD point): record vs broker entry/exit price
    pnl_tolerance: float = Field(0.01, ge=0)              # account currency per trade (audit RC-9: 0.01 per trade)
    close_grace_minutes: int = Field(15, ge=0, le=1440)   # a broker close this recent may not be recorded yet
    max_age_hours: float = Field(48.0, gt=0)              # health warns when the last ledger report is older


class TickRecordSettings(_Section):
    """The broker tick record (goldbot/data/ticks.py, BACKLOG 79; the H-A holiday diagnostic). Research data only: no
    trading decision reads it."""
    # H-A reads 2026-10-18 .. 2027-04-30 with controls 14 days either side, after the window closes: the first THIN day
    # (2026-11-26) must still be on disk in mid-May 2027. 240 days of under 1 MB a day is under 200 MB an account
    retain_days: int = Field(240, ge=2, le=3650)
    min_london_ny_ticks: int = Field(1000, ge=0)          # health WARN below this (the account classifier's bar)
    backfill_days: int = Field(7, ge=0, le=30)            # closed server days before the latest that a run re-checks
    # bridge load (trading-safety review): the bridge serves one request at a time and refuses an engine order older
    # than ORDER_MAX_AGE_S (10 s), so a page must stay short and the run bounded
    max_days_per_run: int = Field(1, ge=1, le=31)          # oldest missing day first; the weekend runs catch up
    page_ticks: int = Field(2000, ge=100, le=10_000)       # rows per copy_ticks call (~120 KB of JSON)
    pause_s: float = Field(0.2, ge=0, le=5)                # between pages: the engine's quote reads get the bridge
    page_limit_s: float = Field(2.0, gt=0, le=8)           # a slower page stops the run; the day is pulled next run
    run_budget_s: float = Field(300.0, gt=0, le=1800)      # per account per run; an unfinished day is not written

    @model_validator(mode="after")
    def _retention_outlives_the_backfill(self) -> TickRecordSettings:
        """A day pruned while still inside the backfill would be pulled again every run and deleted again."""
        if self.retain_days <= self.backfill_days + 1:
            raise ValueError(f"ticks.retain_days ({self.retain_days}) must exceed backfill_days + 1 "
                             f"({self.backfill_days + 1})")
        return self


class RetiredFamily(_Section):
    """A specialist family the strategy researcher retired (docs/research/hypotheses.md section B mirrors this list;
    tests/test_director.py checks they agree). Governance lives here, not in the markdown: editing the doc never
    changes the research director's allocation by itself."""
    family: str                           # specialist family name (goldbot/specialists)
    hypothesis_id: str                    # its row in hypotheses.md section B, e.g. "R-01"
    retired: date                         # reinstatement reads only out-of-sample trades entered on or after this day
    reason: str
    trials: list[int] = Field(default_factory=list)       # registry trial numbers that retired it


class ResearchSettings(_Section):
    trial_budget_per_month: int = Field(12, ge=1, le=200)
    label_grid_paused: bool = True        # the monthly label-grid loop runs only when this is false (proposal P2)
    trial_budget_quarter: int = Field(20, ge=1, le=500)   # pre-registered trials per calendar quarter, all families
    # raises the budget of ONE quarter ({"2026Q4": 53}); a quarter not listed keeps trial_budget_quarter, a value must
    # be >= it. Every trial is still recorded and the deflated Sharpe still counts the full K (an owner decision)
    trial_budget_overrides: dict[str, int] = Field(default_factory=dict)
    # continuous research loop (owner, 2026-10-18; docs/plans/continuous-research-loop.md): trials one scheduled batch may
    # run, SEPARATE from the quarterly budget above (which is not changed). Every loop trial is still a registry row, so
    # the deflated Sharpe counts the full running K. 0 turns the loop off.
    loop_trials_per_run: int = Field(16, ge=0, le=256)
    # account size the loop's sizing pre-filter uses (ATR only, no returns): GBP 1,000 at about 1.30 USD per GBP
    loop_equity_usd: float = Field(1300.0, gt=0)
    # trials held for the pre-registered queue (director.reserved_trials), PER QUARTER ({"2027Q1": 9}; a quarter not
    # listed holds none): the research director, the label grid and the analyst spend only budget - used - reserved
    reserved_trials_quarter: dict[str, int] = Field(default_factory=dict)
    # quarters under the E-5 cap: the analyst spends the open budget only on an ad-hoc pre-registration naming a
    # pre-registered rule (`rule_ref`), plus the retired families' one shared exploration trial; the label grid none
    open_budget_preregistered_only: list[str] = Field(default_factory=list)
    retired_families: list[RetiredFamily] = Field(default_factory=list)
    holdout_from: date | None = date(2025, 10, 1)          # research never sees this window unless scoring it
    holdout_to: date | None = date(2026, 9, 30)            # inclusive
    # primary-signal screen event floor (research.screen, P4); changing it is an owner decision, set before the run
    screen_min_events: int = Field(1000, ge=1)
    # rules whose signal is on daily bars: retire-only (a pass still needs screen_min_events); None: the floor above
    screen_min_events_daily: int | None = Field(None, ge=1)
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

    @field_validator("reserved_trials_quarter")
    @classmethod
    def _quarter_keys(cls, v: dict[str, int]) -> dict[str, int]:
        import re
        for q, n in v.items():
            if not re.fullmatch(r"\d{4}Q[1-4]", q) or not 0 <= n <= 500:
                raise ValueError(f"reserved_trials_quarter maps a quarter like 2027Q1 to 0..500 trials, got {q!r}: {n!r}")
        return v

    @model_validator(mode="after")
    def _budget_overrides(self) -> ResearchSettings:
        import re
        for q, n in self.trial_budget_overrides.items():
            if not re.fullmatch(r"\d{4}Q[1-4]", q) or not self.trial_budget_quarter <= n <= 500:
                raise ValueError(f"trial_budget_overrides maps a quarter like 2026Q4 to trial_budget_quarter "
                                 f"({self.trial_budget_quarter})..500 trials, got {q!r}: {n!r}")
        return self

    def budget_for(self, quarter: str) -> int:
        """The quarter's pre-registered trial budget: its override when it has one, else trial_budget_quarter."""
        return int(self.trial_budget_overrides.get(quarter, self.trial_budget_quarter))

    @field_validator("open_budget_preregistered_only")
    @classmethod
    def _quarters(cls, v: list[str]) -> list[str]:
        import re
        bad = [q for q in v if not re.fullmatch(r"\d{4}Q[1-4]", q)]
        if bad:
            raise ValueError(f"open_budget_preregistered_only lists quarters like 2027Q1, got {bad}")
        return v

    @model_validator(mode="after")
    def _holdout_window_complete(self) -> ResearchSettings:
        """Quant review 2026-10-16 N2: a holdout bound without the other, or a start after the end, is no holdout window,
        so research would read the held-out year as ordinary history ("research never sees this window unless scoring
        it"). Refused at load time rather than first inside a discovery run (research/discovery.py fails closed too)."""
        if (self.holdout_from is None) != (self.holdout_to is None):
            raise ValueError("research.holdout_from and research.holdout_to are set together or not at all")
        if self.holdout_from is not None and self.holdout_to is not None and self.holdout_from > self.holdout_to:
            raise ValueError("research.holdout_from is after research.holdout_to")
        return self

    def reserved_for(self, quarter: str) -> int:
        """Trials of `quarter` held for its pre-registered queue (0 for a quarter not listed)."""
        return int(self.reserved_trials_quarter.get(quarter, 0))

    def open_budget_capped(self, quarter: str) -> bool:
        """`quarter` is under the E-5 cap (open_budget_preregistered_only)."""
        return quarter in self.open_budget_preregistered_only

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
    # daily owner digest (goldbot/telegram/digest.py): sent once a day at this UTC time ("HH:MM"), before London
    digest_at: str = Field("06:45", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    # the owner's time zone for Telegram /status times (UTC is shown beside each); an unknown zone shows UTC only
    owner_tz: str = "Europe/London"
    # outside dead man's switch (goldbot/ops/deadman.py, ADR 0014): healthy pings at most this often; the check's
    # period on Healthchecks.io must match (grace 3 min), so a dead brain is reported within ~6 min
    deadman_every_min: int = Field(2, ge=1, le=60)


_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class MailSettings(_Section):
    """Outgoing mail for the owner's email sign-in link (goldbot/api/mailer.py): plain SMTP with STARTTLS (Gmail:
    smtp.gmail.com:587 and a Google App Password). Off unless enabled, with every address set only in the server's
    git-ignored config/settings.local.yaml (the repo is public). The SMTP password is never a setting: it lives in the
    OS keyring as `smtp-password` (`python -m goldbot.ops.accounts set smtp-password`)."""
    enabled: bool = False
    host: str | None = None
    port: int = Field(587, ge=1, le=65535)                  # STARTTLS submission port
    from_address: str | None = None
    username: str | None = None                             # the SMTP login; unset means from_address
    dashboard_origin: str | None = None                     # https://dash.example.com: the link the email carries
    timeout_s: float = Field(10.0, gt=0, le=60)             # connect + each SMTP command

    @field_validator("dashboard_origin")
    @classmethod
    def _origin(cls, v: str | None) -> str | None:
        """An origin only (the link carries a sign-in token): https with a host and no user info, path, query or
        fragment; http only for exactly localhost, 127.0.0.1 or ::1 (local dev). No whitespace, control character,
        backslash or semicolon anywhere (urlsplit strips or tolerates some of them, browsers others). One trailing
        slash is dropped; the result is the normalised lower-case `scheme://netloc`."""
        if v is None:
            return None
        bad = ValueError("auth.mail.dashboard_origin must be an origin like https://dash.example.com (no path, "
                         "query, fragment, user info, spaces or control characters; http only for localhost)")
        if any(c.isspace() or not c.isprintable() or c in "\\;" for c in v):
            raise bad
        v = v[:-1] if v.endswith("/") and not v.endswith("//") else v
        try:
            u = urlsplit(v)
            u.port                                          # noqa: B018 - raises on a malformed port
        except ValueError:
            raise bad from None
        if not u.netloc or not u.hostname or u.username is not None or u.password is not None or u.path \
                or u.query or u.fragment or "#" in v or "?" in v:
            raise bad
        scheme = u.scheme.lower()
        if scheme == "https" or (scheme == "http" and u.hostname in _LOCAL_HOSTS):
            return f"{scheme}://{u.netloc.lower()}"
        raise bad

    @model_validator(mode="after")
    def _complete_when_enabled(self) -> MailSettings:
        if self.enabled:
            missing = [k for k in ("host", "from_address", "dashboard_origin") if not getattr(self, k)]
            if missing:
                raise ValueError(f"auth.mail is enabled but {', '.join(missing)} is not set")
        return self


class AuthSettings(_Section):
    """Dashboard owner. The repo is public: set owner_email only in the server's git-ignored
    config/settings.local.yaml. Unset, the owner account cannot be created (bootstrap refuses)."""
    owner_email: str | None = None
    mail: MailSettings = Field(default_factory=MailSettings)
    # where the owner's one-time sign-in code goes (ADR 0028 as amended 2026-10-17, goldbot/api/code_delivery.py):
    # `telegram` sends it to the owner's private chat with the bot (keyring `telegram-owner-chat-id`, which must be in
    # telegram.allowed_user_ids); `mail` uses the SMTP path of `mail` above
    code_channel: Literal["telegram", "mail"] = "telegram"


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
    cusum_k: float = Field(0.5, ge=0)                # CUSUM allowance in sd (row M25: h is tuned for this k)
    cusum_false_alarm: float = Field(0.05, gt=0, lt=1)   # design: alarm probability per quarter on in-control trades
    cusum_h: float = Field(4.0, gt=0)                # fallback h when the backtest has no trade rate
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
    # OWNER DECISION (recommended 0.05): the most of the paper / live trades whose return is not net (commission or swap
    # unknown, charged an imputed cost: ops/cost_imputation.py) before the paper and live gates are not met. None = off:
    # the share is shown for context only
    max_gross_share: float | None = Field(None, gt=0, le=1)
    # The PROPOSED thresholds above the owner has signed off (field names); every other one is shown as proposed on
    # the dashboard's Progress tab. Signing off is the owner's decision: this list only records it and changes no value
    signed_off: list[str] = Field(default_factory=list)

    # the thresholds marked "PROPOSED: owner sign-off required" (the design does not commit their numbers)
    PROPOSED: ClassVar[tuple[str, ...]] = ("shuffle_auc_tolerance", "feed_max_mismatch_share", "backtest_min_dsr",
                                           "paper_min_days", "live_min_days", "broker_min_trades")

    @field_validator("signed_off")
    @classmethod
    def _only_proposed(cls, v: list[str]) -> list[str]:
        """Only a proposed threshold can be signed off: a misspelt or design-committed name fails at load."""
        bad = sorted(set(v) - set(cls.PROPOSED))
        if bad:
            raise ValueError(f"not a proposed gate threshold: {', '.join(bad)} (known: {', '.join(cls.PROPOSED)})")
        return v

    def pending_sign_off(self) -> list[str]:
        """The proposed thresholds not yet signed off, in settings order."""
        return [n for n in self.PROPOSED if n not in self.signed_off]


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
    backup: BackupSettings = Field(default_factory=BackupSettings)
    attribution: AttributionSettings = Field(default_factory=AttributionSettings)
    ledger: LedgerSettings = Field(default_factory=LedgerSettings)
    ticks: TickRecordSettings = Field(default_factory=TickRecordSettings)

    @field_validator("feature_day_anchor", "risk_day_anchor")
    @classmethod
    def _day_anchor_is_the_fixed_day(cls, v: DayAnchor, info: ValidationInfo) -> DayAnchor:
        """Pinned, not wired (BACKLOG 57): no code reads these two settings. The loss caps reset at 00:00 UTC
        (`timeutil.risk_day`: gate `new_day`, throttle, the runner, entry_status, the supervisor) and daily bars and
        features close at 13:30 New York (`timeutil.feature_day`, `resample`'s defaults), whatever the yaml says. Any
        other value is refused at load, so an owner never believes the caps' reset or the daily bar has moved. Moving
        either day is a separate owner decision that needs one shared day function and a trading-safety review."""
        tz, time, what = _FIXED_DAY_ANCHORS[info.field_name or ""]
        if (v.tz, v.time) != (tz, time):
            raise ValueError(f"{info.field_name} is fixed at {tz} {time} (got {v.tz} {v.time}): {what} and this "
                             f"setting cannot move that. Delete {info.field_name} from config/settings.local.yaml.")
        return v

    @model_validator(mode="after")
    def _walkforward_has_purge_and_embargo(self) -> Settings:
        """Every walk-forward timeframe the retrain trains on has its purge and embargo (design: purged, embargoed
        walk-forward), so a window added without them fails at load."""
        for key in ("purge_days", "embargo_days"):
            missing = sorted(set(self.walkforward) - set(getattr(self.labels, key)))
            if missing:
                raise ValueError(f"labels.{key} has no value for walk-forward timeframe(s) {', '.join(missing)}")
        return self


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
