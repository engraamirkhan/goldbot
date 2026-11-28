"""Quant ruling on PR #514 (item 7, re-verify A): the D-12 rho bindings do not carry over to the xt-v1 exit geometry
automatically. A rho measurement file is admitted for a Stage 0, gate or power read only if it records the xt-v1 exit
freeze sha it was measured under; settings load and the learn-window fit-only reads keep the recorded D-12 file.
The one entry point a Stage 0 / power read calls is `data_sufficiency.require_power_read_rho`."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from goldbot.config import PowerFirstSettings, check_rho_measurement
from goldbot.research import data_sufficiency as DS
from goldbot.research import dense_labels as D
from tests.research.test_power_first import CLASSES, rho_file

TFS = ["1m", "5m", "1h"]


def _made(tmp_path: Path, name: str, **over: object) -> tuple[Path, str]:
    """A D-12-style file; one that records an exit_freeze_sha also names `binds_with` (required since #525 F4): a
    prior at the repo-relative path prior.json under the repo root the `xt_root` fixture patched to `tmp_path`."""
    d = tmp_path / name
    d.mkdir()
    if "exit_freeze_sha" in over:
        prior = tmp_path / "prior.json"
        prior.write_text(json.dumps({"rho_day_le_60m": dict.fromkeys(CLASSES, 0.01)}))
        over = {"binds_with": {"path": "prior.json", "sha256": hashlib.sha256(prior.read_bytes()).hexdigest()},
                **over}
    return rho_file(d, **over)


@pytest.fixture()
def xt_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import goldbot.config as C
    monkeypatch.setattr(C, "ROOT", tmp_path)
    return tmp_path


def _xt_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A complete D-12-xt file (per-tf binding, cells, binds_with) from the script's own writer."""
    from tests.research.test_measure_rho_xt import _produce
    return _produce(tmp_path, monkeypatch)[0]


def _inst_file(tmp_path: Path, name: str, sha: str | None) -> Path:
    b = {f"fx10|{bk}": {"max": v} for bk, v in (("le60m", 0.3), ("1h-4h", 0.2), ("gt4h", 0.25))}
    d: dict[str, Any] = {"binding": {"rho_u_inst_day_mean": b}}
    if sha is not None:
        d["exit_freeze_sha"] = sha
    f = tmp_path / f"{name}.json"
    f.write_text(json.dumps(d))
    return f


def _cfg(src_file: Path) -> dict[str, Any]:
    src = {"file": str(src_file), "key": "fx10", "stat": "max"}
    return {"tfs": TFS,
            "units": {"fx10": {"learn": [1000, 1000, 1000], "judge": [500, 500, 500],
                               "card_pooled": {"k": 10, "rho_inst": 0.5, "rho_inst_from": src}},
                      "gold": {"learn": [100, 100, 100], "judge": None}},
            "pooled": {"gold_fx": {"rho_inst_from": src}}}


def test_a_rho_file_without_the_xt_v1_exit_freeze_sha_is_refused_for_a_gate_read(tmp_path: Path, xt_root: Path) -> None:
    f, sha = _made(tmp_path, "none")
    check_rho_measurement(str(f), 0.05, sha)                    # fit-only / settings load: admitted as recorded
    with pytest.raises(ValueError, match="exit_freeze_sha"):
        check_rho_measurement(str(f), 0.05, sha, gate_read=True, freeze_sha=D.EXIT_FREEZE_SHA256)
    other, osha = _made(tmp_path, "other", exit_freeze_sha="0" * 64)
    with pytest.raises(ValueError, match="exit_freeze_sha"):
        check_rho_measurement(str(other), 0.05, osha, gate_read=True, freeze_sha=D.EXIT_FREEZE_SHA256)
    xt, xsha = _made(tmp_path, "xt", exit_freeze_sha=D.EXIT_FREEZE_SHA256)
    check_rho_measurement(str(xt), 0.05, xsha, gate_read=True, freeze_sha=D.EXIT_FREEZE_SHA256)


def test_measured_rho_inst_refuses_a_pre_xt_v1_file_for_a_gate_read(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    old = _cfg(_inst_file(tmp_path, "pre_xt", None))
    src = old["units"]["fx10"]["card_pooled"]["rho_inst_from"]
    assert DS.measured_rho(old, src) == {"1m": 0.3, "5m": 0.2, "1h": 0.25}       # fit-only: the recorded value
    with pytest.raises(ValueError, match="exit_freeze_sha"):
        DS.measured_rho(old, src, gate_read=True)
    with pytest.raises(ValueError, match="exit_freeze_sha"):
        DS.unit_rows(old, "fx10", gate_read=True)
    with pytest.raises(ValueError, match="exit_freeze_sha"):
        DS._rho_row(old, old["pooled"]["gold_fx"], gate_read=True)
    assert DS.unit_rows(old, "fx10")[0] is not None
    other = _cfg(_inst_file(tmp_path, "other", "0" * 64))
    with pytest.raises(ValueError, match="exit_freeze_sha"):
        DS.measured_rho(other, other["units"]["fx10"]["card_pooled"]["rho_inst_from"], gate_read=True)
    xt = _cfg(_xt_file(tmp_path, monkeypatch))
    xsrc = xt["units"]["fx10"]["card_pooled"]["rho_inst_from"]
    assert DS.measured_rho(xt, xsrc, gate_read=True) == DS.measured_rho(xt, xsrc)
    assert DS.unit_rows(xt, "fx10", gate_read=True) == DS.unit_rows(xt, "fx10")


def test_one_entry_point_checks_every_rho_a_power_read_uses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`require_power_read_rho(cfg, power)`: every rho_inst_from file of the sufficiency config and the power
    settings' measured rho_day file must carry the xt-v1 sha; the 0.25 prior reads no file."""
    xt_inst = _cfg(_xt_file(tmp_path, monkeypatch))
    DS.require_power_read_rho(xt_inst, PowerFirstSettings())
    with pytest.raises(ValueError, match="exit_freeze_sha"):
        DS.require_power_read_rho(_cfg(_inst_file(tmp_path, "pre_xt", None)), PowerFirstSettings())
    f, sha = _made(tmp_path, "day_none")
    pre = PowerFirstSettings.model_validate({"rho_day_le_60m": 0.05, "rho_day_measurement": str(f),
                                             "rho_day_measurement_sha256": sha})
    with pytest.raises(ValueError, match="exit_freeze_sha"):
        DS.require_power_read_rho(xt_inst, pre)
    x, xsha = _made(tmp_path, "day_xt", exit_freeze_sha=D.EXIT_FREEZE_SHA256)
    DS.require_power_read_rho(xt_inst, PowerFirstSettings.model_validate(
        {"rho_day_le_60m": 0.05, "rho_day_measurement": str(x), "rho_day_measurement_sha256": xsha}))
    assert not hasattr(PowerFirstSettings, "require_gate_read_rho")     # one entry point, not two


def test_bodies_on_pre_xt_v1_rho_are_flagged_fit_only(tmp_path: Path) -> None:
    assert DS.pre_xt_v1_rho(_cfg(_inst_file(tmp_path, "d12", None))) == ["fx10", "gold_fx"]
    assert DS.pre_xt_v1_rho(_cfg(_inst_file(tmp_path, "xt", D.EXIT_FREEZE_SHA256))) == []
    assert DS.PRE_XT_V1_FLAG == "pre-xt-v1 ρ_u,inst: fit-only, not a power read"


def test_the_rendered_sufficiency_table_flags_exactly_the_pre_xt_v1_bodies() -> None:
    import yaml

    from goldbot.config import ROOT
    cfg = yaml.safe_load((ROOT / "config" / "data" / "sufficiency.yaml").read_text())
    flagged = DS.pre_xt_v1_rho(cfg)
    md = (ROOT / "docs" / "data" / "sufficiency.md").read_text()
    for body in flagged:
        assert f"`{body}`: {DS.PRE_XT_V1_FLAG}" in md
    # D-12-xt moved the shipped bodies to the xt-v1 file: none is flagged and the table carries no flag line
    assert flagged == [] and DS.PRE_XT_V1_FLAG not in md
