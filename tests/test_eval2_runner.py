"""EVAL-2 runner integrity (BACKLOG 149 and 150; `goldbot/research/eval2/runner.py`), with the quant review of
f18881ab (holdout bound from the sim's bars, batch membership from the frozen card by hash, withdrawn or expired
pre-registrations refused, one call path, the self-test module checked by path and hash).

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

import ast
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.research.catalogue import run as catalogue_run
from goldbot.research.catalogue.core import EB
from goldbot.research.catalogue.data import DAY, HOUR, TF_NS, Bars
from goldbot.research.eval2 import confirm as C
from goldbot.research.eval2 import runner as R
from goldbot.research.registry import TrialRegistry

ROOT = Path(__file__).resolve().parents[1]
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
                       "decision_ns": day * DAY, "bound_end_ns": day * DAY + 2 * DAY, "net_r": net,
                       "twin_mean_net_r": twin, "excess_r": net - twin, "cost_r": 0.05})
    df["year"] = 2008 + (np.arange(n) * 2 // n)
    return df


def _card(family: str = "C03", bars: int | None = 10, seed_key: str = "seed") -> R.FamilyCard:
    return R.FamilyCard(family=family, carried=("a",), weights={"a": 1.0}, cells={"a": ("1h", bars)},
                        mean_block=2.0, seed_key=seed_key)


def _frozen(tmp_path: Path, cards: list[R.FamilyCard], window: str = "test_old",
            name: str = "card.json") -> tuple[Path, str]:
    p = tmp_path / name
    return p, R.freeze_batch_card(p, window=window, cards=cards)  # type: ignore[arg-type]


def _snap(*paths: Path) -> list[bytes]:
    return [p.read_bytes() if p.exists() else b"" for p in paths]


# ------------------------------------------------------------------ 150: harness gate before any read
@pytest.mark.parametrize("case", REFUSALS)
def test_an_l3_batch_is_not_read_unless_the_harness_self_tests_passed(selftest_dir, state, tmp_path, monkeypatch,
                                                                      case):
    _refusal_case(selftest_dir, case)
    reg, led = state
    card, sha = _frozen(tmp_path, [_card()])
    before = _snap(reg, led)
    monkeypatch.setattr(C, "family_cv_p", lambda *a, **k: pytest.fail("a real cell was read"))
    with pytest.raises(R.ReadRefused, match="self-test"):
        R.record_batch_p(_units(), card_path=card, card_sha256=sha, batch=C.BatchPRDS("test_old"), j=1, ledger=led)
    assert _snap(reg, led) == before


@pytest.mark.parametrize("case", REFUSALS)
def test_an_l3_read_does_not_start_unless_the_harness_self_tests_passed(selftest_dir, state, tmp_path, monkeypatch,
                                                                        case):
    _refusal_case(selftest_dir, case)
    reg, led = state
    card, sha = _frozen(tmp_path, [_card()])
    before = _snap(reg, led)
    monkeypatch.setattr(C, "l3_read", lambda *a, **k: pytest.fail("the L3 read started"))
    with pytest.raises(R.ReadRefused, match="self-test"):
        R.run_l3_read(_units(), family="C03", card_path=card, card_sha256=sha, batch=C.BatchPRDS("test_old"), j=1,
                      corroboration=None, e9=None, ledger=led)
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


def test_the_gate_never_trusts_a_cached_module_of_the_self_test_name(selftest_dir, monkeypatch):
    """Quant LOW: a module named eval2_selftest that is not the file at scripts/eval2_selftest.py (or not its current
    bytes) is never asked whether the harness is sane."""
    fake = types.ModuleType("eval2_selftest")
    fake.harness_hash = lambda: "h"                        # type: ignore[attr-defined]
    fake.assert_harness_sane = lambda hh, **k: {}          # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "eval2_selftest", fake)
    assert R.selftest() is not fake
    with pytest.raises(R.ReadRefused, match="self-test"):      # no report in selftest_dir: the real check refuses
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


GUARDED = {"l3_read", "family_cv_p", "forward_state"}
ALLOWED = {"goldbot/research/eval2/runner.py", "scripts/eval2_selftest.py"}


def test_only_the_runner_calls_the_l3_and_forward_reads():
    """Quant LOW: every read of a real cell goes through the runner's gates; a call of l3_read, family_cv_p or
    forward_state anywhere else in goldbot/ or scripts/ fails this test."""
    bad = []
    for p in [*ROOT.glob("goldbot/**/*.py"), *ROOT.glob("scripts/**/*.py")]:
        rel = p.relative_to(ROOT).as_posix()
        if rel in ALLOWED:
            continue
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
                if name in GUARDED:
                    bad.append(f"{rel}:{node.lineno} {name}")
    assert not bad, bad


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
                                  "not a pre-registration", "withdrawn", "expired"])
def test_a_futility_read_whose_registry_row_is_not_a_live_prior_preregistration_raises(tmp_path, case):
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
    elif case == "withdrawn":
        TrialRegistry(reg_path).withdraw(row, "never formed: no events")
        read_ts = datetime.now(timezone.utc) + timedelta(seconds=1)
    elif case == "expired":                 # an ad-hoc pre-registration past STALE_PREREG_DAYS already counts as a trial
        read_ts = datetime.fromisoformat(row["ts"]) + timedelta(days=8)
    with pytest.raises(R.ReadRefused, match="pre-regist"):
        R.checked_futility_terms(TrialRegistry(reg_path), family=family, config=config, registry_row=rid,
                                 statistic="gross", mean_r=-0.05, se_r=0.02, read_ts=read_ts)


# ------------------------------------------------------------------ 149 (b): the holdout cut at the sim's bound
def _gold_4h(first: str, last: str) -> Bars:
    """4h gold bars on weekdays only (no weekend bars), opens 00:00..20:00 UTC."""
    days = pd.bdate_range(first, last, tz="UTC")
    t = np.array([int(d.value) + h * HOUR for d in days for h in range(0, 24, 4)], np.int64)
    n = len(t)
    one = np.ones(n)
    return Bars("XAUUSD", "4h", t, t + 4 * HOUR, one, one, one, one, np.full(n, 0.3), np.zeros(n, bool),
                np.zeros(n, np.int32), one)


def _events(b: Bars, at: list[str], hold: int, valid: int = 1, kind: int = 0) -> Any:
    eb = EB()
    for s in at:
        i = int(np.searchsorted(b.t, int(pd.Timestamp(s, tz="UTC").value)))
        eb.add(i, 1, kind=kind, px=1.0, valid=valid, stop_d=1.0, hold=hold)
    return eb.build()


def test_a_thursday_4h_entry_with_a_30_bar_exit_reaching_october_is_dropped():
    """Quant HIGH: Thu 2025-09-25 00:00, 4h, time exit 30 bars. 30 x 4h of calendar time ends 2025-09-30 (the old
    cut kept it); counted in trading bars past the weekend, plus decision -> fill, it ends in October."""
    b = _gold_4h("2025-09-01", "2025-10-17")
    ev = _events(b, ["2025-09-01", "2025-09-25"], hold=30)       # events in bar order: [Sep 1, Sep 25]
    end = R.unit_bound_end_ns(b, ev, ev.i)
    assert end[1] >= HOLDOUT_NS and end[0] < HOLDOUT_NS
    dec = b.ta[ev.i]
    assert int(dec[1]) + 30 * TF_NS["4h"] < HOLDOUT_NS          # the calendar reading would have kept it
    u = pd.DataFrame({"cell_id": "a", "family": "C03", "tf": "4h", "decision_ns": dec, "bound_end_ns": end})
    card = R.FamilyCard(family="C03", carried=("a",), weights={"a": 1.0}, cells={"a": ("4h", 30)}, mean_block=2.0,
                        seed_key="s")
    assert R.cut_at_holdout(u, card)["decision_ns"].tolist() == [int(dec[0])]


def test_the_bound_counts_the_order_validity_and_a_bar_beyond_the_data_is_never_inside():
    b = _gold_4h("2025-09-01", "2025-09-30")                    # sealed data: no bar at or after the holdout
    ev = _events(b, ["2025-09-01"], hold=5, valid=3, kind=1)
    end = R.unit_bound_end_ns(b, ev, ev.i)
    assert end[0] == int(b.t[int(ev.i[0]) + 3 + 5 + 1])          # bar i + valid + hold + 1
    late = _events(b, ["2025-09-29"], hold=30)
    assert R.unit_bound_end_ns(b, late, late.i)[0] >= HOLDOUT_NS  # beyond the bars: unknown, never inside


def test_units_without_the_sim_bound_are_refused():
    u = pd.DataFrame({"cell_id": "a", "family": "C03", "tf": "1h", "decision_ns": [17_600 * DAY]})
    with pytest.raises(R.ReadRefused, match="bound_end_ns"):
        R.cut_at_holdout(u, _card(bars=10))


def test_a_cell_with_no_registered_maximum_hold_is_refused_not_silently_cut():
    """BACKLOG 207 (quant ruling): every carried cell registers its sim hold as its time exit before L2."""
    u = pd.DataFrame({"cell_id": "a", "family": "C03", "tf": "1h", "decision_ns": [17_600 * DAY],
                      "bound_end_ns": [17_601 * DAY]})
    with pytest.raises(R.ReadRefused, match="maximum hold"):
        R.cut_at_holdout(u, _card(bars=None))


def test_the_l3_batch_never_reads_an_instance_whose_bound_reaches_the_holdout(sane, state, tmp_path, monkeypatch):
    _, led = state
    u = _units(n_days=120, day0=17_600)
    late = u.iloc[[0]].assign(decision_ns=HOLDOUT_NS - 30 * HOUR, bound_end_ns=HOLDOUT_NS + HOUR,
                              day=C.HOLDOUT_FROM_DAY - 2)
    seen: list[pd.DataFrame] = []
    real = C.family_cv_p

    def spy(units: pd.DataFrame, **k: Any) -> float:
        seen.append(units)
        return real(units, **k)
    monkeypatch.setattr(C, "family_cv_p", spy)
    card, sha = _frozen(tmp_path, [_card()], window="confirm")
    R.record_batch_p(pd.concat([u, late], ignore_index=True), card_path=card, card_sha256=sha,
                     batch=C.BatchPRDS("confirm"), j=15, ledger=led)
    assert seen and int(seen[0]["bound_end_ns"].max()) < HOLDOUT_NS


# ------------------------------------------------------------------ 149 (c): batch p written first, read back
def _two(tmp_path: Path) -> tuple[pd.DataFrame, Path, str]:
    units = pd.concat([_units(seed=1), _units(seed=2, mu=0.0, family="S21")], ignore_index=True)
    card, sha = _frozen(tmp_path, [_card("C03", seed_key="c03"), _card("S21", seed_key="s21")])
    return units, card, sha


def test_the_batch_p_is_written_to_the_reads_ledger_before_any_l3_read_and_read_back_from_it(sane, state, tmp_path):
    _, led = state
    units, card, sha = _two(tmp_path)
    batch = C.BatchPRDS("test_old")
    written = R.record_batch_p(units, card_path=card, card_sha256=sha, batch=batch, j=1, ledger=led)
    row = [json.loads(x) for x in led.read_text().splitlines()][-1]
    assert row["kind"] == R.BATCH_P_KIND and row["m_j"] == 2 and row["card_sha256"] == sha
    assert [f for f, _ in row["p"]] == ["C03", "S21"] and set(row["units_sha256"]) == {"C03", "S21"}
    assert R.read_batch_p(led, window="test_old", j=1) == written
    res = R.run_l3_read(units, family="C03", card_path=card, card_sha256=sha, batch=batch, j=1, corroboration=None,
                        e9=None, ledger=led)
    assert res.p_cv_net == written["C03"]          # l3_read found its own p in the ledger's batch_p, to the bit


def test_batch_membership_comes_from_the_frozen_card_not_the_caller(sane, state, tmp_path):
    """Quant MED: m_j is the card's family count; a card whose bytes changed after freezing, or a hash that is not the
    card's, is refused before anything is read or written."""
    _, led = state
    units, card, sha = _two(tmp_path)
    before = _snap(led)
    with pytest.raises(R.ReadRefused, match="card"):
        R.record_batch_p(units, card_path=card, card_sha256="0" * 64, batch=C.BatchPRDS("test_old"), j=1, ledger=led)
    doc = json.loads(card.read_text())
    doc["l3_batch"]["body"]["families"].pop()                     # a family dropped after the freeze
    card.write_text(json.dumps(doc))
    with pytest.raises(R.ReadRefused, match="card"):
        R.record_batch_p(units, card_path=card, card_sha256=sha, batch=C.BatchPRDS("test_old"), j=1, ledger=led)
    assert _snap(led) == before


def test_a_card_frozen_for_another_window_is_refused(sane, state, tmp_path):
    _, led = state
    card, sha = _frozen(tmp_path, [_card()], window="confirm")
    with pytest.raises(R.ReadRefused, match="window"):
        R.record_batch_p(_units(), card_path=card, card_sha256=sha, batch=C.BatchPRDS("test_old"), j=1, ledger=led)


def test_a_batch_card_is_frozen_once(tmp_path):
    card, _ = _frozen(tmp_path, [_card()])
    with pytest.raises(R.ReadRefused, match="already"):
        R.freeze_batch_card(card, window="test_old", cards=[_card(), _card("S21")])


def test_an_l3_read_on_other_units_than_the_batch_p_was_computed_on_is_refused(sane, state, tmp_path, monkeypatch):
    _, led = state
    units, card, sha = _two(tmp_path)
    R.record_batch_p(units, card_path=card, card_sha256=sha, batch=C.BatchPRDS("test_old"), j=1, ledger=led)
    monkeypatch.setattr(C, "l3_read", lambda *a, **k: pytest.fail("the L3 read started"))
    other = units.assign(net_r=units["net_r"] + 0.01)
    with pytest.raises(R.ReadRefused, match="units"):
        R.run_l3_read(other, family="C03", card_path=card, card_sha256=sha, batch=C.BatchPRDS("test_old"), j=1,
                      corroboration=None, e9=None, ledger=led)


def test_an_l3_read_without_its_batch_p_in_the_ledger_is_refused(sane, state, tmp_path, monkeypatch):
    _, led = state
    card, sha = _frozen(tmp_path, [_card()])
    monkeypatch.setattr(C, "l3_read", lambda *a, **k: pytest.fail("the L3 read started"))
    with pytest.raises(R.ReadRefused, match="batch p"):
        R.run_l3_read(_units(), family="C03", card_path=card, card_sha256=sha, batch=C.BatchPRDS("test_old"), j=1,
                      corroboration=None, e9=None, ledger=led)


def test_a_family_outside_the_frozen_card_is_refused(sane, state, tmp_path, monkeypatch):
    _, led = state
    card, sha = _frozen(tmp_path, [_card()])
    R.record_batch_p(_units(), card_path=card, card_sha256=sha, batch=C.BatchPRDS("test_old"), j=1, ledger=led)
    monkeypatch.setattr(C, "l3_read", lambda *a, **k: pytest.fail("the L3 read started"))
    with pytest.raises(R.ReadRefused, match="S21"):
        R.run_l3_read(_units(family="S21"), family="S21", card_path=card, card_sha256=sha,
                      batch=C.BatchPRDS("test_old"), j=1, corroboration=None, e9=None, ledger=led)


def test_a_batch_p_is_written_once_and_never_rewritten(sane, state, tmp_path):
    _, led = state
    card, sha = _frozen(tmp_path, [_card()])
    R.record_batch_p(_units(), card_path=card, card_sha256=sha, batch=C.BatchPRDS("test_old"), j=1, ledger=led)
    before = _snap(led)
    with pytest.raises(R.ReadRefused, match="already"):
        R.record_batch_p(_units(seed=9), card_path=card, card_sha256=sha, batch=C.BatchPRDS("test_old"), j=1,
                         ledger=led)
    assert _snap(led) == before


def test_batch_p_rows_do_not_count_as_reads_of_a_window(sane, state, tmp_path, monkeypatch):
    """`read_batch_index` and `_claim` count rows by their `window` key; a batch-p row must move neither."""
    reg, led = state
    monkeypatch.setattr(catalogue_run, "default_registry", lambda: reg)
    j0 = catalogue_run.read_batch_index("test_old")
    card, sha = _frozen(tmp_path, [_card()])
    R.record_batch_p(_units(), card_path=card, card_sha256=sha, batch=C.BatchPRDS("test_old"), j=1, ledger=led)
    assert catalogue_run.read_batch_index("test_old") == j0
    assert led == reg.with_name(catalogue_run.READS_LEDGER)


def test_the_default_ledger_is_the_catalogue_reads_ledger(monkeypatch, tmp_path):
    reg = tmp_path / "state" / "research_registry.jsonl"
    monkeypatch.setattr(catalogue_run, "default_registry", lambda: reg)
    assert R.default_ledger() == tmp_path / "state" / "catalogue_reads.jsonl"
