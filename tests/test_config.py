import pytest
import yaml
from pydantic import ValidationError

from goldbot.config import DEFAULT_SETTINGS, Settings, load_settings


def test_repo_settings_validate():
    s = load_settings()
    assert s.features.max_live_features <= 40
    assert set(s.walkforward) == {"15m", "1h"}
    assert s.risk.daily_cap < s.risk.weekly_cap


def _raw() -> dict:
    with open(DEFAULT_SETTINGS, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


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
