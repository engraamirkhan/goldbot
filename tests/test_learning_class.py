"""The demo learning class (owner 2026-11-02: "fix the limits of our system to trade ... for the system to be not bound
to test, learn and improve signals"; the owner answered YES to a demo "learning" class; ADR 0061 amendment
2026-11-02). A `role: learning` row may enter on a demo account without a history qualification, only when:

* its `learning.k_id` is this arm's K row (derived from the arm, `arms.learning_k_id`) and resolves in the registry;
* its `learning.prereg` doc exists in the repo;
* its `learning.control` names a matched control row (same timeframe, stop, target and hold);
* its `learning.until` is at most LEARNING_MAX_DAYS (45) days away, and not past;
* it has no qualification block (and, on the account, no growth sizing and no pyramid adds).

A dedicated control (`params.control: "learning:<ids>"`) is refused with the learning arms it serves once none of them
may enter. A qualification that cites a learning K row is refused: unqualified demo evidence never qualifies. The
perf report and the daily review show learning arms in their own section, never in the family rankings."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml

from goldbot.engine import exploration
from goldbot.ops import accounts as acc_mod
from goldbot.research import arms
from tests.test_exploration_engine import QUALIFIED
from tests.test_learning_mi import MI_ARMS, MI_CONTROLS

TODAY = pd.Timestamp("2026-11-02", tz="UTC")
PREREG = "docs/research/learning-2026-11-02.md"
SPEC = Path(__file__).resolve().parents[1] / "config" / "exploration_arms.yaml"
RULE = arms.LEARNING_RULE
# the 2026-11-02 registration: every arm the qualification gate refused that has a fixed bracket, and its control
# quant ruling 2026-11-02 (2)/(3): every learning arm has its own dedicated control "LC<arm>"
LEARNING = {acc: {a: "LC" + a for a in ids} for acc, ids in {
    "icm-demo": ("Z3D", "Z4D", "Z6D", "Z7D", "Z9D", "Z10D"),
    "icm-probe": ("S5A", "S5B", "Z1P", "Z2P", "Z4P", "Z5P", "Z7P"),
    "vantage-demo": ("Z6V", "Z7V")}.items()}
# refused by the qualification gate and left out: no fixed bracket a random control can match (a runner exit, a
# specialist's own stop, a VWAP target or a stop band)
NO_BRACKET = {"BXR15", "BXR30", "BXR1H", "BXR4H", "XBB15", "S1A", "S1B"}


@pytest.fixture(autouse=True)
def _today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arms, "_now", lambda: TODAY)


def _body(arm_id: str, *, tf: str = "5m", stop: float = 4.5, target: float = 1.5, hold: int = 6,
          rule: str = "tsmom", **params: Any) -> arms.Arm:
    p: dict[str, Any] = {"stop_usd": stop, "target_r": target, "hold_bars": hold, **params}
    if rule == "tsmom":
        p["config"] = {}
    return arms.Arm(id=arm_id, rule=rule, timeframe=tf, params=p)  # type: ignore[arg-type]


def _row(a: arms.Arm, **extra: Any) -> dict[str, Any]:
    return {**a.model_dump(exclude={"entry_refused"}), "config_hash": arms.arm_hash(a), **extra}


def _learning_row(a: arms.Arm, control: str = "C1", **change: Any) -> dict[str, Any]:
    block = {"prereg": PREREG, "k_id": arms.learning_k_id(a.id, arms.arm_hash(a), PREREG), "control": control,
             "loss_cap_gbp": 130.0, "until": "2026-12-17", "since": "2026-11-02", **change}
    return _row(a, role="learning", learning=block)


def _control(arm_id: str = "C1", *, serves: str | None = None, **kw: Any) -> dict[str, Any]:
    params: dict[str, Any] = {"minutes": [0], "window_utc": ["07:00", "19:00"]}
    if serves is not None:
        params["control"] = arms.LEARNING_CONTROL_PREFIX + serves
    c = _body(arm_id, rule="random_bracket", **{**params, **kw})
    return _row(c, role="control")


def _registry(tmp_path: Path, rows: list[dict[str, Any]], learning_family: bool = True) -> Path:
    """A registry with one K row per learning row of `rows` (its k_id as the config hash)."""
    reg = tmp_path / "research_registry.jsonl"
    lines = [json.dumps({"trial": i + 1, "config_hash": r["learning"]["k_id"], "status": "learning",
                         "family": arms.LEARNING_FAMILY if learning_family else "other"})
             for i, r in enumerate(rows) if r.get("role") == "learning" and isinstance(r.get("learning"), dict)
             and "k_id" in r["learning"]]
    reg.write_text("\n".join(lines) + "\n")
    return reg


def _load(tmp_path: Path, rows: list[dict[str, Any]], registry: Path | None = None) -> dict[str, arms.Arm]:
    path = tmp_path / "arms.yaml"
    path.write_text(yaml.safe_dump({"arms": rows}))
    reg = registry if registry is not None else _registry(tmp_path, rows)
    return {a.id: a for a in arms.load_arms(path, registry=reg)}


# ------------------------------------------------------------------------------------------------ the row
def test_a_learning_row_with_its_k_row_prereg_doc_and_matched_control_may_enter(tmp_path) -> None:
    got = _load(tmp_path, [_learning_row(_body("L1")), _control("C1")])
    assert got["L1"].entry_refused == "" and got["C1"].entry_refused == ""


def test_the_learning_block_and_the_role_are_outside_the_frozen_hash(tmp_path) -> None:
    a = _body("L1")
    b = arms.Arm.model_validate(_learning_row(a))
    assert arms.arm_hash(a) == arms.arm_hash(b) == b.config_hash


def test_a_learning_row_whose_k_row_is_not_in_the_registry_is_refused(tmp_path) -> None:
    rows = [_learning_row(_body("L1")), _control("C1")]
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    for reg in (empty, tmp_path / "missing.jsonl"):
        got = _load(tmp_path, rows, reg)["L1"]
        assert got.entry_refused.startswith(RULE) and "k_id" in got.entry_refused, got.entry_refused


def test_a_k_id_that_is_not_this_arms_own_k_row_is_refused_even_when_the_registry_has_it(tmp_path) -> None:
    other = _learning_row(_body("L2"))
    row = _learning_row(_body("L1"), k_id=other["learning"]["k_id"])
    got = _load(tmp_path, [row, _control("C1")], _registry(tmp_path, [other]))["L1"]
    assert got.entry_refused.startswith(RULE) and "k_id" in got.entry_refused


def test_a_learning_row_whose_prereg_doc_does_not_exist_is_refused(tmp_path) -> None:
    for prereg in ("docs/research/no-such-learning.md", "../outside.md", "registry:abc"):
        a = _body("L1")
        row = _learning_row(a, prereg=prereg, k_id=arms.learning_k_id(a.id, arms.arm_hash(a), prereg))
        got = _load(tmp_path, [row, _control("C1")])["L1"]
        assert got.entry_refused.startswith(RULE) and "prereg" in got.entry_refused, prereg


@pytest.mark.parametrize("control, why", [
    ({"tf": "15m"}, "timeframe"),              # a 1m row would not load at all (ruling 2026-11-13: no new 1m arm)
    ({"stop": 3.0}, "stop"),
    ({"target": 2.0}, "target"),
    ({"hold": 12}, "hold"),
])
def test_a_learning_row_needs_a_matched_control_same_timeframe_stop_target_and_hold(
        tmp_path, control: dict[str, Any], why: str) -> None:
    got = _load(tmp_path, [_learning_row(_body("L1")), _control("C1", **control)])["L1"]
    assert got.entry_refused.startswith(RULE) and why in got.entry_refused, got.entry_refused


def test_a_learning_row_naming_no_control_row_or_a_row_that_is_not_a_control_is_refused(tmp_path) -> None:
    miss = _load(tmp_path, [_learning_row(_body("L1"), control="NOPE")])["L1"]
    assert miss.entry_refused.startswith(RULE) and "control" in miss.entry_refused
    strat = _load(tmp_path, [_learning_row(_body("L1"), control="S9"), _row(_body("S9"), qualification=QUALIFIED)])
    assert strat["L1"].entry_refused.startswith(RULE) and "control" in strat["L1"].entry_refused


def test_an_arm_without_a_fixed_bracket_cannot_be_matched_to_a_control(tmp_path) -> None:
    runner = arms.Arm(id="L1", rule="tsmom", timeframe="5m", params={"config": {}, "hold_bars": 6, "stop_usd": 4.5})
    got = _load(tmp_path, [_learning_row(runner), _control("C1")])["L1"]
    assert got.entry_refused.startswith(RULE) and "fixed bracket" in got.entry_refused


@pytest.mark.parametrize("until, why", [("2026-12-18", "45 days"), ("2026-11-01", "ended"), ("someday", "invalid")])
def test_a_learning_row_runs_at_most_45_days_and_never_past_its_until(tmp_path, until: str, why: str) -> None:
    got = _load(tmp_path, [_learning_row(_body("L1"), until=until), _control("C1")])["L1"]
    assert got.entry_refused.startswith(RULE) and why in got.entry_refused, got.entry_refused


def test_a_learning_row_carries_no_qualification_block(tmp_path) -> None:
    row = {**_learning_row(_body("L1")), "qualification": QUALIFIED}
    got = _load(tmp_path, [row, _control("C1")])["L1"]
    assert got.entry_refused.startswith(RULE) and "qualification" in got.entry_refused


@pytest.mark.parametrize("bad", [None, "yes", {"prereg": PREREG}, {"k_id": "x", "loss_cap_gbp": -1}])
def test_a_malformed_learning_block_refuses_its_arm_and_never_stops_the_load(tmp_path, bad: Any) -> None:
    row = {**_learning_row(_body("L1")), "learning": bad}
    got = _load(tmp_path, [row, _learning_row(_body("L2"), control="C1"), _control("C1")])
    assert got["L1"].entry_refused.startswith(RULE) and got["L2"].entry_refused == ""


# ------------------------------------------------------------------------------------------------ controls
def test_a_dedicated_control_is_refused_with_its_learning_arms_and_runs_while_one_of_them_may(tmp_path) -> None:
    ok, bad = _learning_row(_body("L1"), control="C1"), _learning_row(_body("L2"), control="C1", until="2026-11-01")
    ctl = _control("C1", serves="L1,L2")
    both = _load(tmp_path, [ok, bad, ctl])
    assert both["L1"].entry_refused == "" and both["L2"].entry_refused and both["C1"].entry_refused == ""
    alone = _load(tmp_path, [bad, ctl])
    assert alone["C1"].entry_refused.startswith(RULE) and "stops with" in alone["C1"].entry_refused


def test_a_shared_preregistered_control_keeps_its_own_role_when_its_learning_arms_are_refused(tmp_path) -> None:
    got = _load(tmp_path, [_learning_row(_body("L1"), until="2026-11-01"), _control("C1")])
    assert got["L1"].entry_refused and got["C1"].entry_refused == ""


# ------------------------------------------------------------------------------------------------ qualification
def test_a_qualification_citing_a_learning_k_row_is_refused(tmp_path) -> None:
    learn = _learning_row(_body("L1"))
    kid = learn["learning"]["k_id"]
    q = {**QUALIFIED, "confirm": f"registry:{kid}"}
    reg = _registry(tmp_path, [learn])
    got = _load(tmp_path, [learn, _control("C1"), _row(_body("Q1"), qualification=q)], reg)
    assert got["Q1"].entry_refused.startswith(arms.QUALIFY_RULE) and "learning" in got["Q1"].entry_refused
    # the same through the spec alone, when the registry row is not marked as a learning row
    plain = _registry(tmp_path, [learn], learning_family=False)
    got = _load(tmp_path, [learn, _control("C1"), _row(_body("Q1"), qualification=q)], plain)
    assert "learning" in got["Q1"].entry_refused


# ------------------------------------------------------------------------------------------------ the account
def _account(arm_id: str, **reg: Any) -> acc_mod.Account:
    acc = acc_mod.load_accounts(logins=False)["icm-probe"]
    arms_ = {**acc.exploration.arms, arm_id: acc.exploration.arms[arm_id].model_copy(update=reg)}
    return acc.model_copy(update={"exploration": acc.exploration.model_copy(update={"arms": arms_})})


@pytest.mark.parametrize("reg, why", [({"sizing": "growth"}, "growth"),
                                      ({"sizing": "growth", "adds": 1}, "adds")])
def test_a_learning_arm_registered_with_growth_sizing_or_adds_is_refused(reg: dict[str, Any], why: str) -> None:
    cfg = exploration.config_for(_account("Z1P", **reg))
    assert cfg is not None
    refused = exploration.entry_refused(cfg)
    assert refused["Z1P"].startswith(RULE) and why in refused["Z1P"]


# ------------------------------------------------------------------------------------------------ the spec
def _k_script() -> Any:
    import importlib.util
    spec = importlib.util.spec_from_file_location("record_learning_k_rows",
                                                  SPEC.parents[1] / "scripts" / "record_learning_k_rows.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_k_row_script_writes_each_row_once_inside_the_quarters_budget(tmp_path, monkeypatch) -> None:
    from goldbot.research.registry import TrialRegistry
    mod = _k_script()
    reg = tmp_path / "research_registry.jsonl"
    want = mod.k_rows(SPEC)
    # 15 gold arms and their 15 controls, and the 8 multi-instrument pairs (learning-mi-2026-11-02.md)
    assert len(want) == 2 * sum(len(v) for v in LEARNING.values()) + len(MI_ARMS | MI_CONTROLS)
    assert mod.record(reg, SPEC, dry_run=True) and not TrialRegistry(reg)._rows()
    monkeypatch.setattr(TrialRegistry, "check_budget", lambda self, n, cap, now=None: "2026Q4")
    written = mod.record(reg, SPEC)
    rows = TrialRegistry(reg)._rows()
    assert {r["config_hash"] for r in rows} == set(want) and len(written) == len(want)
    assert all(r["family"] == arms.LEARNING_FAMILY and r["status"] == "learning" for r in rows)
    assert mod.record(reg, SPEC) == [] and len(TrialRegistry(reg)._rows()) == len(want)      # idempotent


def test_the_k_row_script_writes_nothing_over_the_quarters_budget(tmp_path, monkeypatch) -> None:
    from goldbot.research.registry import TrialBudgetExceeded, TrialRegistry
    mod = _k_script()
    reg = tmp_path / "research_registry.jsonl"

    def full(self: Any, n: int, cap: int, now: Any = None) -> str:
        raise TrialBudgetExceeded("full")
    monkeypatch.setattr(TrialRegistry, "check_budget", full)
    with pytest.raises(TrialBudgetExceeded):
        mod.record(reg, SPEC)
    assert not reg.exists() or not TrialRegistry(reg)._rows()


@pytest.fixture
def k_rows(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The registry as `scripts/record_learning_k_rows.py` writes it on the brain: one K row per learning row and
    per dedicated control."""
    reg = tmp_path / "research_registry.jsonl"
    reg.write_text("".join(json.dumps({"trial": i + 1, "config_hash": h, "family": arms.LEARNING_FAMILY,
                                       "status": "learning"}) + "\n"
                           for i, (h, _) in enumerate(_k_script().k_rows(SPEC).items())))
    monkeypatch.setattr(arms, "REGISTRY_PATH", reg)
    return reg


def test_the_spec_registers_every_fixed_bracket_arm_the_gate_refused_as_a_learning_row(k_rows) -> None:
    spec = {a.id: a for a in arms.load_arms(SPEC)}
    wanted = {a for per in LEARNING.values() for a in per}
    assert {i for i, a in spec.items() if a.role == "learning"} == wanted | MI_ARMS
    for per in LEARNING.values():
        for arm_id, control in per.items():
            a = spec[arm_id]
            block = arms.Learning.model_validate(a.learning)
            # A5 (signed 2026-11-06) retired some arms with their controls: a retired pair is refused for that reason
            assert a.entry_refused == "" or (a.retired and spec[control].retired), (arm_id, a.entry_refused)
            assert block.control == control and spec[control].role == "control"
            assert arms.control_match_refusal(a, spec[control]) is None
            assert block.prereg == PREREG and block.until == "2026-12-17" and block.since == "2026-11-02"
            assert arms.dedicated_control_of(spec[control]) == (arm_id,)
    for arm_id in NO_BRACKET:
        assert spec[arm_id].role == "strategy" and spec[arm_id].entry_refused.startswith(arms.QUALIFY_RULE)


def test_each_learning_loss_cap_is_twenty_times_its_arms_per_trade_cap() -> None:
    spec = {a.id: a for a in arms.load_arms(SPEC)}
    accounts = acc_mod.load_accounts(logins=False)
    for acc_id, per in LEARNING.items():
        for arm_id in per:
            cap = accounts[acc_id].exploration.arms[arm_id].max_loss
            assert arms.Learning.model_validate(spec[arm_id].learning).loss_cap_gbp == pytest.approx(20 * cap)


def test_the_prereg_doc_lists_every_learning_arm_its_k_id_and_its_control() -> None:
    text = (SPEC.parents[1] / PREREG).read_text()
    spec = {a.id: a for a in arms.load_arms(SPEC)}
    assert "fix the limits of our system to trade" in text
    for per in LEARNING.values():
        for arm_id, control in per.items():
            block = arms.Learning.model_validate(spec[arm_id].learning)
            assert arm_id in text and block.k_id in text and control in text


def test_without_its_k_rows_every_learning_arm_and_dedicated_control_is_refused_fail_closed(tmp_path,
                                                                                            monkeypatch) -> None:
    monkeypatch.setattr(arms, "REGISTRY_PATH", tmp_path / "missing.jsonl")
    spec = {a.id: a for a in arms.load_arms(SPEC)}
    for a in spec.values():
        if a.role == "learning" or str(a.params.get("control", "")).startswith(arms.LEARNING_CONTROL_PREFIX):
            assert a.entry_refused.startswith(RULE), a.id
    assert spec["ZCD"].entry_refused == "" and spec["ZCV"].entry_refused == ""     # H-81's, unchanged


def test_every_learning_arm_is_registered_on_its_account_with_its_control_and_no_growth_or_adds() -> None:
    accounts = acc_mod.load_accounts(logins=False)
    for acc_id, per in LEARNING.items():
        reg = accounts[acc_id].exploration.arms
        for arm_id, control in per.items():
            assert arm_id in reg and control in reg, (acc_id, arm_id)
            assert reg[arm_id].sizing == "min_lot" and reg[arm_id].adds == 0
            assert reg[control].control_share is not None


# ------------------------------------------------------------------------------------------------ reports
def test_the_spec_names_its_learning_arms_for_the_reports() -> None:
    assert arms.learning_arm_ids(SPEC) == frozenset(a for per in LEARNING.values() for a in per) | MI_ARMS


def test_the_perf_report_shows_learning_arms_in_their_own_section_never_in_the_family_rankings(
        tmp_path, enabled_accounts, monkeypatch) -> None:
    from goldbot.ops import perf_report
    from tests.test_learning_reports import _arm as closed
    from tests.test_learning_reports import _engine, _report
    enabled_accounts("icm-probe")
    monkeypatch.setattr(perf_report, "learning_arm_ids", lambda *a, **k: frozenset({"Z1P"}))
    _engine(tmp_path)
    closed(tmp_path, "s1", "S1A")
    closed(tmp_path, "z1", "Z1P", minute=10)
    closed(tmp_path, "z2", "Z1P", minute=20, pnl=-2.4)
    closed(tmp_path, "c1", "S0", minute=30)
    rep = _report(tmp_path)
    [a] = rep.accounts
    assert a.learning is not None
    assert {g.key: g.trades for g in a.learning.kinds} == {"exploration": 1, "control": 1}
    assert all("Z1P" not in g.key for g in a.learning.families)
    [g] = a.learning.unqualified
    assert g.key == "learning / Z1P" and g.trades == 2 and "never ranked" in g.verdict
    md = perf_report.format_markdown(rep)
    assert "Learning (unqualified, demo only)" in md and "learning / Z1P" in md


def test_the_daily_review_counts_learning_fills_in_their_own_line(tmp_path, monkeypatch) -> None:
    from goldbot.research import daily_review as dr
    from tests.test_daily_review import _blinded, _cand, _db, _run, _trade
    monkeypatch.setattr(dr, "learning_arm_ids", lambda *a, **k: frozenset({"Z1P"}))
    db = _db(tmp_path)
    for i, arm in enumerate(("S1A", "Z1P", "Z1P")):
        _cand(db, f"c{i}", arm=arm)
        _trade(db, f"c{i}", arm=arm)
    review = _run(tmp_path, dry_run=True)
    assert review.learning == {"icm": {"Z1P": 2}}
    [line] = [x for x in dr.report_lines(review) if "learning" in x]
    assert "unqualified, demo only" in line and "2 fills" in line and "1 arm" in line
    _blinded(line)                                    # the owner report's blinding: no rank word, no figure in R


def test_the_nightly_bandit_never_allocates_a_learning_arm(tmp_path) -> None:
    from goldbot.db import Database
    from goldbot.research import exploration_data as ex
    db = Database.open(tmp_path, "aux")
    t0 = pd.Timestamp("2026-10-26 08:00", tz="UTC")
    for arm_id in ("S5A", "Z1P"):
        for i in range(6):
            cid = f"{arm_id}-{i}"
            ex.record_candidate(db, candidate_id=cid, account_id="icm-probe", broker="icm", arm_id=arm_id,
                                family="f", style="scalp", tf="1m", side=1,
                                decision_utc=t0 + pd.Timedelta(minutes=i), stop_dist=2.0, target_dist=2.0,
                                max_hold_s=600, taken=True, mu=1.0, branch="random", u=0.5)
            with db.write() as conn:
                conn.execute("UPDATE exploration_obs SET label_sim_r = 1.0, label_mid_r = 1.0, label_exit_ns = ? "
                             "WHERE candidate_id = ?", ((t0 + pd.Timedelta(hours=1)).value, cid))
    out = ex.learn(db, broker="icm", as_of=t0 + pd.Timedelta(days=1), learning={"Z1P"})
    assert set(out["weights"]) == {"S5A"}
