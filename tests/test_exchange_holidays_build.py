"""The committed exchange holiday calendar agrees with its generator's availability rule (BACKLOG 152), checked
without exchange_calendars (a build-time tool, not a project dependency): version string, every ad hoc closure's
notice-derived available_utc, every regular date's prior-year floor. A hand edit of the YAML fails here."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.data import exchange_holidays as XH

ROOT = Path(__file__).resolve().parents[1]


def _gen() -> Any:
    spec = importlib.util.spec_from_file_location("build_exchange_holidays", ROOT / "scripts" / "build_exchange_holidays.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("build_exchange_holidays", mod)
    spec.loader.exec_module(mod)
    return mod


def test_calendar_matches_the_generator_rule() -> None:
    g = _gen()
    cal = XH.load_calendar()
    assert cal.version == f"xcals-{g.XCALS_VERSION}-r{g.REVISION}"
    assert (cal.valid_from, cal.valid_through) == (g.VALID_FROM, g.VALID_THROUGH)
    assert set(cal.exchanges) == set(g.EXCHANGES)
    adhoc_seen = set()
    for ex, rows in cal.exchanges.items():
        for r in rows:
            kind, av, _ = g.available(ex, r.date)
            assert r.kind == kind, (ex, r.date)
            assert r.available_utc == pd.Timestamp(av), (ex, r.date)
            if kind == "adhoc":
                adhoc_seen.add((ex, r.date.isoformat()))
    assert adhoc_seen == set(g.AD_HOC)
