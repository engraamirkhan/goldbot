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
