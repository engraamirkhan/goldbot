"""Quant ruling on PR #514 (item 7): the D-12 rho bindings do not carry over to the xt-v1 exit geometry automatically.
A rho measurement file is admitted for a gate or power read only if it records the xt-v1 exit freeze sha it was
measured under; settings load (learn-window fit-only reads) keeps admitting the recorded D-12 file."""
from __future__ import annotations

from pathlib import Path

import pytest

from goldbot.config import PowerFirstSettings, check_rho_measurement
from goldbot.research import dense_labels as D
from tests.research.test_power_first import rho_file


def _made(tmp_path: Path, name: str, **over: object) -> tuple[Path, str]:
    d = tmp_path / name
    d.mkdir()
    return rho_file(d, **over)


def test_a_rho_file_without_the_xt_v1_exit_freeze_sha_is_refused_for_a_gate_read(tmp_path: Path) -> None:
    f, sha = _made(tmp_path, "none")
    check_rho_measurement(str(f), 0.05, sha)                    # fit-only / settings load: admitted as recorded
    with pytest.raises(ValueError, match="exit_freeze_sha"):
        check_rho_measurement(str(f), 0.05, sha, gate_read=True)
    other, osha = _made(tmp_path, "other", exit_freeze_sha="0" * 64)
    with pytest.raises(ValueError, match="exit_freeze_sha"):
        check_rho_measurement(str(other), 0.05, osha, gate_read=True)
    xt, xsha = _made(tmp_path, "xt", exit_freeze_sha=D.EXIT_FREEZE_SHA256)
    check_rho_measurement(str(xt), 0.05, xsha, gate_read=True)


def test_power_settings_refuse_a_pre_xt_v1_rho_file_for_a_gate_read(tmp_path: Path) -> None:
    f, sha = _made(tmp_path, "none")
    pf = PowerFirstSettings.model_validate({"rho_day_le_60m": 0.05, "rho_day_measurement": str(f),
                                            "rho_day_measurement_sha256": sha})
    with pytest.raises(ValueError, match="exit_freeze_sha"):
        pf.require_gate_read_rho()
    xt, xsha = _made(tmp_path, "xt", exit_freeze_sha=D.EXIT_FREEZE_SHA256)
    PowerFirstSettings.model_validate({"rho_day_le_60m": 0.05, "rho_day_measurement": str(xt),
                                       "rho_day_measurement_sha256": xsha}).require_gate_read_rho()
    PowerFirstSettings().require_gate_read_rho()               # the 0.25 prior reads no measurement file
