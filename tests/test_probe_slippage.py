"""Scheduled-probe slippage raises, never lowers, the charged slippage (prereg-cost-measurement-2026-10 s0.3/s5,
h1 refit spec s2.4-2.5): "Demo slippage only raises costs"; probe fills never enter `fills` (K7)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.config import load_settings
from goldbot.data.store import Store
from goldbot.db import Database
from goldbot.engine import cost_probe
from goldbot.engine.cost_probe import ProbeRecord
from goldbot.execution.costs import CostTable, probe_slippage
from goldbot.ops.accounts import Account
from goldbot.ops.jobs import JobContext, nightly_costs
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.population import Population
from goldbot.research.registry import TrialRegistry

SETTINGS = load_settings()
PRIOR = SETTINGS.costs.slippage_prior_usd
NOW = pd.Timestamp("2026-10-23 23:10", tz="UTC")
ACC = Account(account_id="icm-demo", broker="icm", mode="demo", server="ICMarketsSC-Demo", login=None, terminal_path="",
              symbol="XAUUSD", server_tz="Europe/Athens", magic_base=260100, enabled=True)
LONDON = 10      # UTC hour inside the London session


def _rows(n: int, slip: float, *, hour: int = LONDON, spread: float = 0.02, **kw: Any) -> list[ProbeRecord]:
    """n alternating-side entry fills, one a day at `hour`, each worse (positive) or better than requested by about
    `slip` $/oz (a deterministic wobble so the day-block bootstrap has a width)."""
    wobble = np.random.default_rng(1).uniform(-spread, spread, n)
    out = []
    for i in range(n):
        side = 1 if i % 2 == 0 else -1
        px = 2000.0
        out.append(ProbeRecord(
            client_order_id=f"cm-{hour}-{i}", account_id="icm-demo", purpose="cost_measurement", agent_id="sched",
            side=side, lots=0.01, requested_price=px, fill_price=px + side * (slip + wobble[i]),
            sent_utc=pd.Timestamp("2026-08-01", tz="UTC") + pd.Timedelta(days=i, hours=hour), retcode=10009, **kw))
    return out


def test_probe_slippage_charges_max_of_prior_and_mean():
    high = probe_slippage(_rows(60, 0.30), PRIOR)["london"]
    assert high.raised and high.charged == high.mean and high.mean > PRIOR and high.ci_low > PRIOR
    # the quant ruling: a mean above the prior is charged even when its interval still covers the prior
    wide = probe_slippage(_rows(60, 0.17, spread=0.6), PRIOR)["london"]
    assert wide.raised and wide.charged == wide.mean > PRIOR and wide.ci_low < PRIOR < wide.ci_high
    # favourable slippage is reported, never credited
    fav = probe_slippage(_rows(60, -0.07), PRIOR)["london"]
    assert not fav.raised and fav.charged == PRIOR and fav.mean < 0 and fav.n == 60


def test_below_min_fills_keeps_prior():
    assert probe_slippage(_rows(49, 0.30), PRIOR) == {}   # the prereg's 50 per session, not the sketch's 30
    assert probe_slippage(_rows(50, 0.30), PRIOR)["london"].raised
    # no pooling across sessions to reach the minimum
    assert probe_slippage(_rows(30, 0.30) + _rows(30, 0.30, hour=3), PRIOR) == {}


def test_only_done_entry_cost_measurement_rows_count():
    rows = _rows(60, 0.30)
    breaks: list[dict[str, Any]] = [{"retcode": 10006}, {"fill_price": None}, {"requested_price": None},
                                    {"purpose": "cost_probe"}]
    bad = [r.model_copy(update=breaks[i % 4]) for i, r in enumerate(rows)]
    assert probe_slippage(bad, PRIOR) == {}      # 60 rows, every one disqualified


def _ctx(tmp_path: Path) -> JobContext:
    return JobContext(settings=SETTINGS, store=Store(tmp_path / "data"), state_dir=tmp_path,
                      models=ModelRegistry(tmp_path / "models"), trials=TrialRegistry(tmp_path / "trials.jsonl"),
                      accounts=[ACC], population=Population(tmp_path / "population.json"))


def _write(tmp_path: Path, rows: list[ProbeRecord]) -> None:
    db = Database.open(tmp_path, "aux")
    with db.write() as conn:
        for r in rows:
            cost_probe.LEDGER.upsert(conn, r)


def test_nightly_costs_raise_the_session_slippage_from_probes(tmp_path):
    _write(tmp_path, _rows(60, 0.30))
    out = nightly_costs(_ctx(tmp_path), NOW)["icm-demo"]
    table = CostTable.load(tmp_path / "costs_icm-demo.json")
    assert table is not None
    cell = table.slippage["london:market"]
    assert not cell.from_prior and cell.mean > PRIOR
    assert table.slippage["asia:market"].from_prior and table.slippage["asia:market"].mean == PRIOR
    assert table.probe_slippage["london"].raised and out["probe_fills"] == 60


def test_unreadable_aux_db_keeps_prior(tmp_path):
    (tmp_path / "aux.db").write_bytes(b"not a sqlite file at all" * 50)
    out = nightly_costs(_ctx(tmp_path), NOW)["icm-demo"]
    table = CostTable.load(tmp_path / "costs_icm-demo.json")
    assert table is not None and table.probe_slippage == {}
    assert all(c.from_prior and c.mean == PRIOR for c in table.slippage.values())
    assert out["probe_fills"] == 0


def test_probes_never_in_fills(tmp_path):
    _write(tmp_path, _rows(60, 0.30))
    ctx = _ctx(tmp_path)
    out = nightly_costs(ctx, NOW)["icm-demo"]
    table = CostTable.load(tmp_path / "costs_icm-demo.json")
    assert out["fills"] == 0 and table is not None and table.n_fills == 0
    assert ctx.store.read("fills", source="icm-demo", symbol="XAUUSD").empty


def test_a_probe_sent_after_the_slot_does_not_change_the_table(tmp_path):
    late = [r.model_copy(update={"sent_utc": NOW + pd.Timedelta(hours=1 + i)}) for i, r in enumerate(_rows(60, 0.30))]
    _write(tmp_path, late)
    nightly_costs(_ctx(tmp_path), NOW)
    table = CostTable.load(tmp_path / "costs_icm-demo.json")
    assert table is not None and table.probe_slippage == {}
