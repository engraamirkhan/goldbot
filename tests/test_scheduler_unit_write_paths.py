"""The scheduler runs under ProtectSystem=strict, so every directory a scheduled job writes must be in ReadWritePaths.

Incident 2026-11-26: broker_m1_copy (ADR 0105 (ii)) writes raw/broker_m1/ and failed with EROFS on the brain because
/opt/goldbot/raw was not writable for the unit (health FAIL job:broker_m1_copy, learning:broker_m1_copy).
"""
from __future__ import annotations

from pathlib import Path

from goldbot.data import broker_capture as BC

REPO = Path(__file__).resolve().parents[1]
UNIT = REPO / "goldbot/ops/linux/systemd/goldbot-scheduler.service"
ROOT = "/opt/goldbot"


def _write_paths() -> list[str]:
    out: list[str] = []
    for line in UNIT.read_text().splitlines():
        if line.startswith("ReadWritePaths="):
            out += [p.lstrip("-") for p in line.split("=", 1)[1].split()]
    return out


def test_the_broker_m1_copy_target_is_writable_for_the_scheduler() -> None:
    target = f"{ROOT}/{BC.RAW_DIR.relative_to(REPO).as_posix()}"
    assert any(target == p or target.startswith(p + "/") for p in _write_paths()), target


def test_the_raw_path_is_optional_so_a_host_without_raw_still_starts() -> None:
    # A missing ReadWritePaths entry stops the unit from starting unless it carries the "-" prefix.
    line = next(x for x in UNIT.read_text().splitlines() if x.startswith("ReadWritePaths="))
    assert f"-{ROOT}/raw" in line.split("=", 1)[1].split()
