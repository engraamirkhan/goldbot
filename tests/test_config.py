import pytest
import yaml
from pydantic import ValidationError

from goldbot.config import DEFAULT_SETTINGS, Settings, load_settings


def test_repo_settings_validate():
    s = load_settings()
    assert s.features.max_live_features <= 40
    assert set(s.walkforward) == {"15m", "1h", "4h"}
    assert s.risk.daily_cap < s.risk.weekly_cap


def _raw() -> dict:
    with open(DEFAULT_SETTINGS, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_4h_walkforward_window_matches_the_research_window():
    # proposal P4: 4h train 48 / test 6 / step 6 months, purge 10 d, embargo 4 d (research.walkforward.WINDOWS["4h"])
    s = load_settings()
    w = s.walkforward["4h"]
    assert (w.train_months, w.test_months, w.step_months) == (48, 6, 6)
    assert (s.labels.purge_days["4h"], s.labels.embargo_days["4h"]) == (10, 4)
    assert "4h" in s.timeframes.decision


def test_1d_walkforward_window_is_rejected():
    # 1d stays research-only: the engine keeps ~40 trading days of 1m bars, short of the 120 daily bars a frame needs
    raw = _raw()
    raw["walkforward"]["1d"] = {"train_months": 60, "test_months": 12, "step_months": 12}
    with pytest.raises(ValidationError):
        Settings.model_validate(raw)


@pytest.mark.parametrize("key", ["purge_days", "embargo_days"])
def test_walkforward_timeframe_without_purge_or_embargo_is_rejected(key):
    raw = _raw()
    del raw["labels"][key]["4h"]
    with pytest.raises(ValidationError, match="4h"):
        Settings.model_validate(raw)


def test_unknown_key_is_rejected():
    raw = _raw()
    raw["risk"]["daly_cap"] = 0.02   # misspelt key must fail loudly
    with pytest.raises(ValidationError):
        Settings.model_validate(raw)


@pytest.mark.parametrize("path,value", [
    (("features", "max_live_features"), 41),       # design cap
    (("risk", "risk_per_trade"), 0.05),            # above the 1% ceiling
    (("timeframes", "decision"), ["5m"]),          # not a decision timeframe
])
def test_out_of_range_values_are_rejected(path, value):
    raw = _raw()
    raw[path[0]][path[1]] = value
    with pytest.raises(ValidationError):
        Settings.model_validate(raw)


def test_duplicate_keys_are_rejected(tmp_path):
    text = open(DEFAULT_SETTINGS, encoding="utf-8").read()
    dup = tmp_path / "s.yaml"
    dup.write_text(text + "\nagents:\n  monthly_cap_usd: 1.0\n")
    with pytest.raises(yaml.constructor.ConstructorError, match="duplicate key 'agents'"):
        load_settings(dup)


# BACKLOG 57: the day anchors are pinned, not wired. The loss caps reset at 00:00 UTC (timeutil.risk_day) and the
# daily bars close at 13:30 New York (timeutil.feature_day) whatever settings.yaml says, so any other value is refused
# at load instead of letting the owner believe the reset or the daily bar has moved.
_RISK_ANCHOR_ERROR = ("risk_day_anchor is fixed at UTC 00:00 (got Europe/London 22:00): the loss caps reset at "
                      "00:00 UTC in code (timeutil.risk_day) and this setting cannot move that. Delete "
                      "risk_day_anchor from config/settings.local.yaml.")
_FEATURE_ANCHOR_ERROR = ("feature_day_anchor is fixed at America/New_York 13:30 (got UTC 00:00): daily bars and "
                         "features close at 13:30 New York in code (timeutil.feature_day) and this setting cannot "
                         "move that. Delete feature_day_anchor from config/settings.local.yaml.")


def test_the_shipped_settings_load_with_the_fixed_day_anchors():
    s = load_settings()
    assert (s.risk_day_anchor.tz, s.risk_day_anchor.time) == ("UTC", "00:00")
    assert (s.feature_day_anchor.tz, s.feature_day_anchor.time) == ("America/New_York", "13:30")


@pytest.mark.parametrize("key,anchor,error", [
    ("risk_day_anchor", {"tz": "Europe/London", "time": "22:00"}, _RISK_ANCHOR_ERROR),
    ("feature_day_anchor", {"tz": "UTC", "time": "00:00"}, _FEATURE_ANCHOR_ERROR),
], ids=["risk_day_anchor", "feature_day_anchor"])
def test_an_edited_day_anchor_in_settings_local_fails_to_load_naming_the_fixed_day(tmp_path, key, anchor, error):
    (tmp_path / "settings.yaml").write_text(DEFAULT_SETTINGS.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "settings.local.yaml").write_text(yaml.safe_dump({key: anchor}), encoding="utf-8")
    with pytest.raises(ValidationError) as exc:
        load_settings(tmp_path / "settings.yaml")
    assert error in str(exc.value)


@pytest.mark.parametrize("key,anchor", [
    ("risk_day_anchor", {"tz": "UTC", "time": "22:00"}),               # time moved
    ("risk_day_anchor", {"tz": "Europe/London", "time": "00:00"}),     # zone moved
    ("risk_day_anchor", {"tz": "Etc/UTC", "time": "00:00"}),           # same instant, other spelling: still refused
    ("feature_day_anchor", {"tz": "America/New_York", "time": "17:00"}),
    ("feature_day_anchor", {"tz": "America/Chicago", "time": "13:30"}),
])
def test_any_day_anchor_other_than_the_fixed_one_is_rejected(key, anchor):
    raw = _raw()
    raw[key] = anchor
    with pytest.raises(ValidationError, match=f"{key} is fixed at"):
        Settings.model_validate(raw)


def test_the_risk_day_and_so_the_loss_cap_reset_is_pinned_at_00_utc():
    """No change to when any cap resets: `new_day` resets the day-start equity at 00:00 UTC, not a moment before."""
    import pandas as pd

    from goldbot.risk.gate import AccountState, new_day

    st = AccountState(equity=1000.0, balance_closed_hwm=1000.0, day_start_equity=1000.0, week_start_equity=1000.0,
                      open_positions=0, margin_used=0.0, last_tick_age_s=0.0, spread_points=0.0)
    last = pd.Timestamp("2026-10-05 00:05", tz="UTC")
    st.equity = 990.0
    assert new_day(st, pd.Timestamp("2026-10-05 23:59:59.999", tz="UTC"), last) is False
    assert st.day_start_equity == 1000.0
    assert new_day(st, pd.Timestamp("2026-10-06 00:00", tz="UTC"), last) is True
    assert st.day_start_equity == 990.0


def test_a_holdout_start_without_a_holdout_end_fails_to_load(tmp_path):
    """Quant review 2026-10-16 N2: `holdout_from` without `holdout_to` leaves no holdout window, so research would
    read the held-out year as ordinary history. Refused when the settings load, not first inside a discovery run."""
    (tmp_path / "settings.yaml").write_text(DEFAULT_SETTINGS.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "settings.local.yaml").write_text(yaml.safe_dump({"research": {"holdout_to": None}}), encoding="utf-8")
    with pytest.raises(ValidationError, match="holdout_to"):
        load_settings(tmp_path / "settings.yaml")


@pytest.mark.parametrize("research,error", [
    ({"holdout_from": None}, "set together or not at all"),                                  # an end without a start
    ({"holdout_from": "2026-09-30", "holdout_to": "2025-10-01"}, "holdout_from is after"),   # inverted window
], ids=["end_only", "inverted"])
def test_a_holdout_window_that_is_half_set_or_inverted_fails_to_load(tmp_path, research, error):
    """Quant review 2026-10-16 (N2 widened): either leaves no holdout window, so research reads the held-out year."""
    (tmp_path / "settings.yaml").write_text(DEFAULT_SETTINGS.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "settings.local.yaml").write_text(yaml.safe_dump({"research": research}), encoding="utf-8")
    with pytest.raises(ValidationError, match=error):
        load_settings(tmp_path / "settings.yaml")


def test_settings_without_any_holdout_window_still_load(tmp_path):
    (tmp_path / "settings.yaml").write_text(DEFAULT_SETTINGS.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "settings.local.yaml").write_text(
        yaml.safe_dump({"research": {"holdout_from": None, "holdout_to": None}}), encoding="utf-8")
    assert load_settings(tmp_path / "settings.yaml").research.holdout_from is None


# ---- per-quarter trial budget override (owner directive 2026-10-18)
def _research(**kw):
    raw = _raw()
    raw["research"].update(kw)
    return Settings.model_validate(raw).research


def test_a_quarter_override_raises_the_budget_of_that_quarter_only():
    r = _research(trial_budget_quarter=20, trial_budget_overrides={"2026Q4": 53})
    assert r.budget_for("2026Q4") == 53
    assert r.budget_for("2027Q1") == 20


def test_an_override_below_the_base_budget_is_rejected():
    with pytest.raises(ValidationError, match="trial_budget_overrides"):
        _research(trial_budget_quarter=20, trial_budget_overrides={"2026Q4": 19})


@pytest.mark.parametrize("key", ["2026-Q4", "2026Q5", "Q4"])
def test_an_override_with_a_malformed_quarter_key_is_rejected(key):
    with pytest.raises(ValidationError, match="trial_budget_overrides"):
        _research(trial_budget_overrides={key: 53})


def test_the_shipped_settings_raise_2026q4_to_55_and_leave_other_quarters_at_the_base():
    r = Settings.model_validate(_raw()).research      # the yaml itself: conftest's base_trial_budget clears the cached copy
    assert r.budget_for("2026Q4") == 55
    assert r.budget_for("2027Q1") == r.trial_budget_quarter


def test_the_director_quarter_budget_follows_the_override():
    from goldbot.research.director import quarter_budget
    r = _research(trial_budget_quarter=20, trial_budget_overrides={"2026Q4": 53})
    assert quarter_budget(r, "2026Q4") == 53
    assert quarter_budget(r, "2027Q1") == 20
