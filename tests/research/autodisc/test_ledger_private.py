"""The discovery ledger (state/discovery_*.db) is a state database: owner-only (0600) like core.db and aux.db, so
preflight's db_files_private check passes after the nightly search first creates it (incident 2026-11-14, the same
trap as health_outages.db)."""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from goldbot.ops import autodisc_register as R
from goldbot.research.autodisc.ledger import SearchLedger
from goldbot.research.autodisc.stages import StageJournal


@pytest.fixture(autouse=True)
def world_readable_umask() -> Iterator[None]:
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


def _private(p: Path) -> bool:
    return p.stat().st_mode & 0o077 == 0


def test_a_new_search_ledger_is_created_owner_only(tmp_path):
    p = tmp_path / "discovery_real.db"
    SearchLedger(p, mode="real")
    assert _private(p)


def test_a_new_stage_journal_is_created_owner_only(tmp_path):
    p = tmp_path / "discovery_real.db"
    StageJournal(p)
    assert _private(p)


def test_the_registration_journal_is_created_owner_only(tmp_path):
    p = tmp_path / "discovery_real.db"
    R._connect(p).close()
    assert _private(p)


def test_a_world_readable_ledger_is_made_owner_only_on_open(tmp_path):
    p = tmp_path / "discovery_real.db"
    SearchLedger(p, mode="real")
    p.chmod(0o644)
    SearchLedger(p, mode="real")
    assert _private(p)
