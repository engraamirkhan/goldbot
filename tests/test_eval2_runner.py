"""EVAL-2 runner integrity (BACKLOG 149 and 150; `goldbot/research/eval2/runner.py`).

149 (Lane 3 quant review, round 2): "(a) the `registry_row` passed to `screen.futility_terms` is looked up in the
registry before the screen reads it: an id with no pre-registered row (same config hash, written before the read)
raises ... (b) the runner cuts the L3 units at decision + the registered maximum hold before 2025-10-01 ... (c) the
runner writes every family's `confirm.family_cv_p` for batch j (m_j of them, `len(batch_p)` equal to the card's m_j)
to `state/catalogue_reads.jsonl` before any `l3_read` of that batch, and `l3_read` reads them back from there."

150: "the Lane 3 L2/L3 runner and the Lane 5A forward reader call [assert_harness_sane] with the current
`harness_hash()` before reading any real cell (`forward=True` for forward reads); a refusal test per runner (no report,
a BLOCKED test, a report for another hash) shows the read does not start and nothing is written to the registry."
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.research.catalogue import run as catalogue_run
from goldbot.research.catalogue.data import DAY, TF_NS
from goldbot.research.eval2 import confirm as C
from goldbot.research.eval2 import runner as R
from goldbot.research.registry import TrialRegistry

TEST_OLD_DAY0 = 13_910          # 2008-01-31
HOLDOUT_NS = C.HOLDOUT_FROM_DAY * DAY
VERDICT = ("S0", "S1", "S2", "S3", "S4", "S6")


# ------------------------------------------------------------------ fixtures
def _report(d: Path, hh: str, *, tests: tuple[str, ...] = (*VERDICT, "S5"), blocked: str | None = None) -> None:
    doc = {"harness_hash": hh, "harness_changed": False, "fast": False,
           "results": [{"test": t, "passed": t != blocked, "metrics": {"blocked": 1.0} if t == blocked else {}}
                       for t in tests]}
    (d / "report_20261113T000000_1.json").write_text(json.dumps(doc))


@pytest.fixture
def selftest_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "selftest"
    d.mkdir()
    monkeypatch.setenv("EVAL2_SELFTEST_DIR", str(d))
    return d


@pytest.fixture
def sane(selftest_dir: Path) -> Path:
    """A full passing self-test report (S0-S6) for the current harness hash."""
    _report(selftest_dir, R.selftest().harness_hash())
    return selftest_dir


@pytest.fixture
def state(tmp_path: Path) -> tuple[Path, Path]:
    """(registry, reads ledger), both with content, so 'nothing written' is a byte comparison."""
    reg = tmp_path / "state" / "research_registry.jsonl"
    reg.parent.mkdir()
    reg.write_text(json.dumps({"trial": 1, "status": "preregistered", "ts": "2026-11-01T00:00:00+00:00"}) + "\n")
    led = reg.with_name("catalogue_reads.jsonl")
    led.write_text(json.dumps({"window": "explore", "ts": "2026-11-08T00:00:00+00:00", "head": "x"}) + "\n")
    return reg, led


REFUSALS = ("no report", "BLOCKED test", "report for another hash")


def _refusal_case(d: Path, case: str) -> None:
    if case == "BLOCKED test":
        _report(d, R.selftest().harness_hash(), blocked="S1")
    elif case == "report for another hash":
        _report(d, "0123456789abcdef")


def _units(n_days: int = 300, per_day: int = 2, mu: float = 0.3, seed: int = 0, family: str = "C03",
           day0: int = TEST_OLD_DAY0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = n_days * per_day
    day = np.repeat(np.arange(n_days), per_day) + day0
    twin = rng.normal(-0.03, 0.2, n)
    net = twin + mu + rng.normal(0.0, 1.0, n)
    df = pd.DataFrame({"cell_id": "a", "family": family, "inst": "XAUUSD", "tf": "1h", "side": 1, "day": day,
                       "decision_ns": day * DAY, "net_r": net, "twin_mean_net_r": twin, "excess_r": net - twin,
                       "cost_r": 0.05})
    df["year"] = 2008 + (np.arange(n) * 2 // n)
    return df


def _card(family: str = "C03", bars: int | None = 10, seed_key: str = "seed") -> R.FamilyCard:
    return R.FamilyCard(family=family, carried=("a",), weights={"a": 1.0}, cells={"a": ("1h", bars)},
                        mean_block=2.0, seed_key=seed_key)


def _snap(*paths: Path) -> list[bytes]:
    return [p.read_bytes() if p.exists() else b"" for p in paths]


# ------------------------------------------------------------------ 150: harness gate before any read
@pytest.mark.parametrize("case", REFUSALS)
def test_an_l3_batch_is_not_read_unless_the_harness_self_tests_passed(selftest_dir, state, monkeypatch, case):
    _refusal_case(selftest_dir, case)
    reg, led = state
    before = _snap(reg, led)
    monkeypatch.setattr(C, "family_cv_p", lambda *a, **k: pytest.fail("a real cell was read"))
    with pytest.raises(R.ReadRefused, match="self-test"):
        R.record_batch_p(_units(), [_card()], batch=C.BatchPRDS("test_old"), j=1, card_m_j=1, ledger=led)
    assert _snap(reg, led) == before


@pytest.mark.parametrize("case", REFUSALS)
def test_an_l3_read_does_not_start_unless_the_harness_self_tests_passed(selftest_dir, state, monkeypatch, case):
    _refusal_case(selftest_dir, case)
    reg, led = state
    before = _snap(reg, led)
    monkeypatch.setattr(C, "l3_read", lambda *a, **k: pytest.fail("the L3 read started"))
    with pytest.raises(R.ReadRefused, match="self-test"):
        R.run_l3_read(_units(), _card(), batch=C.BatchPRDS("test_old"), j=1, corroboration=None, e9=None,
                      ledger=led)
    assert _snap(reg, led) == before


@pytest.mark.parametrize("case", REFUSALS)
def test_an_l2_read_does_not_start_unless_the_harness_self_tests_passed(selftest_dir, state, monkeypatch, case):
    _refusal_case(selftest_dir, case)
    reg, led = state
    before = _snap(reg, led)
    monkeypatch.setattr(R.H, "fit_prior", lambda *a, **k: pytest.fail("the L2 read started"))
    with pytest.raises(R.ReadRefused, match="self-test"):
        R.run_l2_read(pd.DataFrame(), family="C03", spec=None, deciding_cost_r={})    # type: ignore[arg-type]
    assert _snap(reg, led) == before


@pytest.mark.parametrize("case", (*REFUSALS, "S0-S4 and S6 passed but no S5"))
def test_a_forward_read_does_not_start_unless_the_harness_and_s5_self_tests_passed(selftest_dir, state, monkeypatch,
                                                                                   case):
    if case.startswith("S0-S4"):
        _report(selftest_dir, R.selftest().harness_hash(), tests=VERDICT)
    else:
        _refusal_case(selftest_dir, case)
    reg, led = state
    before = _snap(reg, led)
    monkeypatch.setattr(R.FW, "forward_state", lambda *a, **k: pytest.fail("the forward read started"))
    with pytest.raises(R.ReadRefused, match="self-test"):
        R.run_forward_read(pd.DataFrame(), family="C03", l3_shrunk_r=0.1, s5_passed=True)
    assert _snap(reg, led) == before


def test_a_harness_whose_self_test_module_cannot_load_fails_closed(monkeypatch):
    def broken() -> Any:
        raise ImportError("scripts/eval2_selftest.py is missing")
    monkeypatch.setattr(R, "selftest", broken)
    with pytest.raises(R.ReadRefused, match="self-test"):
        R.harness_gate(forward=False)


def test_the_l2_and_forward_reads_run_on_a_sane_harness(sane, monkeypatch):
    calls: list[str] = []

    def fake(name: str, out: Any) -> Any:
        def f(*a: Any, **k: Any) -> Any:
            calls.append(name)
            return out
        return f
    monkeypatch.setattr(R.H, "fit_prior", fake("prior", {}))
    monkeypatch.setattr(R.H, "posteriors", fake("post", []))
    monkeypatch.setattr(R.H, "l2_decide", fake("decide", "decision"))
    monkeypatch.setattr(R.FW, "forward_state", fake("forward", "state"))
    cells = pd.DataFrame({"cell_id": ["a"], "family": ["C03"]})
    l2: Any = R.run_l2_read(cells, family="C03", spec=None, deciding_cost_r={"a": 0.1})  # type: ignore[arg-type]
    fwd: Any = R.run_forward_read(pd.DataFrame(), family="C03", l3_shrunk_r=0.1, s5_passed=True)
    assert (l2, fwd) == ("decision", "state")
    assert calls == ["prior", "post", "decide", "forward"]


# ------------------------------------------------------------------ 149 (a): futility registry row is checked
def _prereg(reg_path: Path, config: dict[str, Any], family: str = "catalogue:C03") -> dict[str, Any]:
    return TrialRegistry(reg_path).preregister(agent_id="strategy-researcher", family=family, config=config,
                                               feature_version="v", rationale="r", reading_rule="futility 4.7")


def test_futility_terms_carry_a_registry_row_only_when_it_preregistered_this_config_before_the_read(tmp_path):
    reg = tmp_path / "research_registry.jsonl"
    cfg = {"unit": "C03|XAUUSD|1h|x", "statistic": "gross"}
    row = _prereg(reg, cfg)
    rid = R.registry_row_id(row)
    later = datetime.fromisoformat(row["ts"]) + timedelta(seconds=1)
    terms = R.checked_futility_terms(TrialRegistry(reg), family="catalogue:C03", config=cfg, registry_row=rid,
                                     statistic="gross", mean_r=-0.05, se_r=0.02, read_ts=later)
    assert terms["registry_row"] == rid and terms["mean_r"] == -0.05


@pytest.mark.parametrize("case", ["empty id", "unknown id", "other config", "other family", "written after the read",
                                  "not a pre-registration"])
def test_a_futility_read_whose_registry_row_is_not_a_prior_preregistration_raises(tmp_path, case):
    reg_path = tmp_path / "research_registry.jsonl"
    cfg = {"unit": "C03|XAUUSD|1h|x", "statistic": "gross"}
    row = _prereg(reg_path, cfg)
    rid, family, config = R.registry_row_id(row), "catalogue:C03", cfg
    read_ts = datetime.fromisoformat(row["ts"]) + timedelta(seconds=1)
    if case == "empty id":
        rid = ""
    elif case == "unknown id":
        rid = "ffffffffffff@2026-01-01T00:00:00+00:00"
    elif case == "other config":
        config = {**cfg, "statistic": "delta"}
    elif case == "other family":
        family = "catalogue:S21"
    elif case == "written after the read":
        read_ts = datetime.fromisoformat(row["ts"]) - timedelta(seconds=1)
    elif case == "not a pre-registration":
        reg_path.write_text(json.dumps({**row, "status": "evaluated"}) + "\n")
    with pytest.raises(R.ReadRefused, match="pre-regist"):
        R.checked_futility_terms(TrialRegistry(reg_path), family=family, config=config, registry_row=rid,
                                 statistic="gross", mean_r=-0.05, se_r=0.02, read_ts=read_ts)


# ------------------------------------------------------------------ 149 (b): the holdout cut at decision + max hold
def test_the_runner_cuts_l3_units_whose_registered_hold_would_reach_the_holdout():
    hold = 10 * TF_NS["1h"]
    dec = np.array([HOLDOUT_NS - hold - 1, HOLDOUT_NS - hold, HOLDOUT_NS - 1, 17_600 * DAY])
    u = pd.DataFrame({"cell_id": "a", "family": "C03", "tf": "1h", "decision_ns": dec})
    out = R.cut_at_holdout(u, _card(bars=10))
    assert sorted(out["decision_ns"].tolist()) == sorted([int(dec[0]), int(dec[3])])


def test_a_cell_with_no_registered_maximum_hold_is_refused_not_silently_cut():
    """An unbounded hold always reaches the holdout: dropping every instance would read the family as NOT CONFIRMED
    (closed for good) on no data. The runner refuses instead."""
    u = pd.DataFrame({"cell_id": "a", "family": "C03", "tf": "1h", "decision_ns": [17_600 * DAY]})
    with pytest.raises(R.ReadRefused, match="maximum hold"):
        R.cut_at_holdout(u, _card(bars=None))


def test_the_l3_read_never_sees_an_instance_whose_hold_reaches_the_holdout(sane, state, monkeypatch):
    """The decision-instant guard in l3_read passes a decision at 2025-09-30 23:00; the runner's cut removes it."""
    _, led = state
    u = _units(n_days=120, day0=17_600)
    late = u.iloc[[0]].assign(decision_ns=HOLDOUT_NS - TF_NS["1h"], day=C.HOLDOUT_FROM_DAY - 1)
    seen: list[pd.DataFrame] = []
    real = C.family_cv_p

    def spy(units: pd.DataFrame, **k: Any) -> float:
        seen.append(units)
        return real(units, **k)
    monkeypatch.setattr(C, "family_cv_p", spy)
    R.record_batch_p(pd.concat([u, late], ignore_index=True), [_card()], batch=C.BatchPRDS("confirm"), j=15,
                     card_m_j=1, ledger=led)
    assert seen and int(seen[0]["decision_ns"].max()) < HOLDOUT_NS - 10 * TF_NS["1h"]


# ------------------------------------------------------------------ 149 (c): batch p written first, read back
def test_the_batch_p_is_written_to_the_reads_ledger_before_any_l3_read_and_read_back_from_it(sane, state):
    _, led = state
    units = pd.concat([_units(seed=1), _units(seed=2, mu=0.0, family="S21")], ignore_index=True)
    cards = [_card("C03", seed_key="c03"), _card("S21", seed_key="s21")]
    batch = C.BatchPRDS("test_old")
    written = R.record_batch_p(units, cards, batch=batch, j=1, card_m_j=2, ledger=led)
    rows = [json.loads(x) for x in led.read_text().splitlines()]
    assert rows[-1]["kind"] == R.BATCH_P_KIND and rows[-1]["m_j"] == 2
    assert [f for f, _ in rows[-1]["p"]] == ["C03", "S21"]
    assert R.read_batch_p(led, window="test_old", j=1) == written
    res = R.run_l3_read(units, cards[0], batch=batch, j=1, corroboration=None, e9=None, ledger=led)
    assert res.p_cv_net == written["C03"]          # l3_read found its own p in the ledger's batch_p, to the bit


def test_an_l3_read_without_its_batch_p_in_the_ledger_is_refused(sane, state, monkeypatch):
    _, led = state
    monkeypatch.setattr(C, "l3_read", lambda *a, **k: pytest.fail("the L3 read started"))
    with pytest.raises(R.ReadRefused, match="batch p"):
        R.run_l3_read(_units(), _card(), batch=C.BatchPRDS("test_old"), j=1, corroboration=None, e9=None, ledger=led)


def test_a_family_outside_the_recorded_batch_is_refused(sane, state, monkeypatch):
    _, led = state
    R.record_batch_p(_units(), [_card()], batch=C.BatchPRDS("test_old"), j=1, card_m_j=1, ledger=led)
    monkeypatch.setattr(C, "l3_read", lambda *a, **k: pytest.fail("the L3 read started"))
    with pytest.raises(R.ReadRefused, match="S21"):
        R.run_l3_read(_units(family="S21"), _card("S21"), batch=C.BatchPRDS("test_old"), j=1, corroboration=None,
                      e9=None, ledger=led)


def test_a_batch_whose_family_count_differs_from_the_card_m_j_is_refused(sane, state):
    _, led = state
    before = _snap(led)
    with pytest.raises(R.ReadRefused, match="m_j"):
        R.record_batch_p(_units(), [_card()], batch=C.BatchPRDS("test_old"), j=1, card_m_j=2, ledger=led)
    assert _snap(led) == before


def test_a_batch_p_is_written_once_and_never_rewritten(sane, state):
    _, led = state
    R.record_batch_p(_units(), [_card()], batch=C.BatchPRDS("test_old"), j=1, card_m_j=1, ledger=led)
    before = _snap(led)
    with pytest.raises(R.ReadRefused, match="already"):
        R.record_batch_p(_units(seed=9), [_card()], batch=C.BatchPRDS("test_old"), j=1, card_m_j=1, ledger=led)
    assert _snap(led) == before


def test_batch_p_rows_do_not_count_as_reads_of_a_window(sane, state, monkeypatch):
    """`read_batch_index` and `_claim` count rows by their `window` key; a batch-p row must move neither."""
    reg, led = state
    monkeypatch.setattr(catalogue_run, "default_registry", lambda: reg)
    j0 = catalogue_run.read_batch_index("test_old")
    R.record_batch_p(_units(), [_card()], batch=C.BatchPRDS("test_old"), j=1, card_m_j=1, ledger=led)
    assert catalogue_run.read_batch_index("test_old") == j0
    assert led == reg.with_name(catalogue_run.READS_LEDGER)


def test_the_default_ledger_is_the_catalogue_reads_ledger(monkeypatch, tmp_path):
    reg = tmp_path / "state" / "research_registry.jsonl"
    monkeypatch.setattr(catalogue_run, "default_registry", lambda: reg)
    assert R.default_ledger() == tmp_path / "state" / "catalogue_reads.jsonl"
