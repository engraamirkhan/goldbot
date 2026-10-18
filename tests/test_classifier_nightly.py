"""The account classifier runs every night while the stored class is unknown, on Fridays only once it is known
(design: Account classifier). A thin first Friday must not block entries for a further week."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.config import load_settings
from goldbot.data.store import Store
from goldbot.ops.accounts import Account
from goldbot.ops.jobs import JobContext, nightly_costs
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.population import Population
from goldbot.research.registry import TrialRegistry

ACC = Account(account_id="icm-demo", broker="icm", mode="demo", server="ICMarketsSC-Demo", login=None, terminal_path="",
              server_tz="Europe/Athens", symbol="XAUUSD", magic_base=260100, enabled=True)


def _ctx(root: Path, tmp_path: Path, acc: Account = ACC) -> JobContext:
    return JobContext(settings=load_settings(), store=Store(root), state_dir=tmp_path, models=ModelRegistry(tmp_path / "models"),
                      trials=TrialRegistry(tmp_path / "trials.jsonl"), accounts=[acc],
                      population=Population(tmp_path / "population.json"))


def _log_ticks(root: Path, start: str, end: str, per_minute: int) -> None:
    """Tight floating-spread ticks (a Raw account's), per_minute of them every minute from start to end."""
    minutes = pd.date_range(start, end, freq="1min", tz="UTC", inclusive="left")
    ts = pd.DatetimeIndex(minutes.repeat(per_minute))
    spread = 0.12 + 0.05 * np.random.default_rng(3).random(len(ts))
    ticks = pd.DataFrame({"ts_utc": ts, "bid": 2400.0, "ask": 2400.0 + spread})
    Store(root).append("ticks", ticks, source="icm-demo", dedupe=False)


def _stored(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "classifier_icm-demo.json").read_text())


def test_nightly_costs_reclassifies_every_night_while_the_class_is_unknown(tmp_path):
    """A thin first Friday stored unknown (entries blocked): the next nights re-run the classifier, not next Friday."""
    root = tmp_path / "data"
    _log_ticks(root, "2025-09-24 12:00", "2025-09-24 12:10", 4)         # a few London ticks: far below 1000
    ctx = _ctx(root, tmp_path)
    wed = nightly_costs(ctx, pd.Timestamp("2025-09-24 23:10", tz="UTC"))["icm-demo"]
    assert wed["account_class"] == "unknown" and "London/NY ticks" in wed["classifier_reason"]
    _log_ticks(root, "2025-09-24 12:10", "2025-09-24 17:00", 4)         # enough ticks by Thursday
    thu = nightly_costs(ctx, pd.Timestamp("2025-09-25 23:10", tz="UTC"))["icm-demo"]
    assert thu["account_class"] in {"raw", "standard"}, thu["classifier_reason"]
    assert _stored(tmp_path)["class"] == thu["account_class"]


def test_nightly_costs_classifies_on_fridays_only_once_a_class_is_known(tmp_path):
    root = tmp_path / "data"
    _log_ticks(root, "2025-09-24 12:00", "2025-09-24 14:00", 4)
    (tmp_path / "classifier_icm-demo.json").write_text(json.dumps({"class": "raw", "pending": None, "runs": []}))
    ctx = _ctx(root, tmp_path)
    thu = nightly_costs(ctx, pd.Timestamp("2025-09-25 23:10", tz="UTC"))["icm-demo"]
    assert "account_class" not in thu and _stored(tmp_path)["runs"] == []
    fri = nightly_costs(ctx, pd.Timestamp("2025-09-26 23:10", tz="UTC"))["icm-demo"]
    assert "account_class" in fri


def test_nightly_costs_reclassifies_nightly_when_the_stored_class_is_the_word_unknown(tmp_path):
    root = tmp_path / "data"
    _log_ticks(root, "2025-09-24 12:00", "2025-09-24 14:00", 4)
    (tmp_path / "classifier_icm-demo.json").write_text(json.dumps({"class": "unknown", "pending": None, "runs": []}))
    wed = nightly_costs(_ctx(root, tmp_path), pd.Timestamp("2025-09-24 23:10", tz="UTC"))["icm-demo"]
    assert "account_class" in wed


def test_a_declared_class_does_not_stop_the_measurement(tmp_path):
    """Declared raw is the baseline, not a verdict: the measurement still runs nightly until it stores a class."""
    root = tmp_path / "data"
    _log_ticks(root, "2025-09-24 12:00", "2025-09-24 14:00", 4)
    declared = ACC.model_copy(update={"account_class": "raw"})
    wed = nightly_costs(_ctx(root, tmp_path, declared), pd.Timestamp("2025-09-24 23:10", tz="UTC"))["icm-demo"]
    assert "account_class" in wed


def test_a_torn_classifier_file_is_recorded_and_the_next_account_still_gets_its_costs(tmp_path):
    root = tmp_path / "data"
    _log_ticks(root, "2025-09-24 12:00", "2025-09-24 14:00", 4)
    (tmp_path / "classifier_icm-demo.json").write_text("{torn")
    other = ACC.model_copy(update={"account_id": "other-demo"})
    ctx = _ctx(root, tmp_path)
    ctx.accounts = [ACC, other]
    out = nightly_costs(ctx, pd.Timestamp("2025-09-24 23:10", tz="UTC"))
    assert "classifier_error" in out["icm-demo"] and "account_class" not in out["icm-demo"]
    assert "other-demo" in out and (tmp_path / "costs_icm-demo.json").exists()
    assert (tmp_path / "classifier_icm-demo.json").read_text() == "{torn"       # never overwritten blindly
