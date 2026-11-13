"""Research-director ruling 2026-11-13 (gold-only demo; live fills n 5,333: non-gold n 1,131 at -GBP 262, -0.21 R a
trade; gold n 4,202 at +GBP 5; 93% of the gold fills on 1m/5m):

1. every demo arm and control whose instrument is not gold (XAUUSD/XAUUSD+) sends no new entry from 2026-11-14 (`retired_day` + `retired_rule` on its own frozen row: id, hash and K stay). Their
   registrations in config/accounts.yaml stay, so an open position keeps every exit (dropping them is backlog 187);
2. no NEW 1m arm: the spec refuses a 1m row whose id is not on the frozen allow list (`arms.ONE_MINUTE_ALLOWED`);
   the 1m arms already in the spec keep running to their reads;
3. A2 readability ("fewer than 2 fills a session day", "fewer than 100 by 2026-11-13") no longer retires an arm of
   1h or slower: those are read on history and the 2018-25 confirm window, not on a four-day demo fill rate.

IEX1H trades gold and stays (quant review of R135: profitability-2026-11-12 3(a) kept it; it is icm-demo's only 1h
fill-vs-replay cost stream). On the engine path (`exploration.config_for`) a row items 2-3 refuse never stops the
start: it stays configured with its entries refused, so open positions keep every exit (trading-safety review of
R135); the research and CI loaders (`load_arms` with the hash check) still refuse the file.
Ruling text: docs/research/ruling-2026-11-13-gold-only.md."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from goldbot.engine import exploration
from goldbot.ops import accounts as acc_mod
from goldbot.research import arms
from tests.test_exploration_engine import _engine, _explore, _probe_account

SPEC = Path(__file__).resolve().parents[1] / "config" / "exploration_arms.yaml"
DAY = "2026-11-14"
GOLD = {"XAUUSD", "XAUUSD+"}
# the rows this ruling retired (the 89 non-gold rows), for the tests of earlier states that must leave them out
RETIRED_1114 = frozenset(a.id for a in arms.load_arms(SPEC) if a.retired_rule == arms.GOLD_ONLY_RULE)


@pytest.fixture(scope="module")
def spec() -> dict[str, arms.Arm]:
    return {a.id: a for a in arms.load_arms(SPEC)}            # hash-checked: a retirement never edits the frozen body


@pytest.fixture(scope="module")
def instruments() -> dict[str, str]:
    return arms.arm_instruments(SPEC)


@pytest.fixture(scope="module")
def accounts() -> dict[str, acc_mod.Account]:
    return acc_mod.load_accounts(logins=False)


def _ruled(a: arms.Arm) -> bool:
    return a.retired_day == DAY and a.retired_rule == arms.GOLD_ONLY_RULE


# ------------------------------------------------------------------------------------------------ 1. gold only
def test_no_arm_or_control_on_an_instrument_other_than_gold_may_enter_after_the_ruling(spec, instruments) -> None:
    for arm_id, a in spec.items():
        if instruments[arm_id] not in GOLD:
            assert a.retired and str(a.retired_day) <= DAY, arm_id


def test_every_non_gold_arm_running_before_the_ruling_is_retired_under_it_with_its_id_and_hash(spec,
                                                                                                instruments) -> None:
    ruled = {i for i, a in spec.items() if _ruled(a)}
    non_gold = {i for i in ruled if instruments[i] not in GOLD}
    assert len(non_gold) == 89
    assert {i.split("-")[0] for i in non_gold} == {"S0", "I0", "LB", "LBC"}
    for arm_id in ruled:
        assert spec[arm_id].config_hash == arms.arm_hash(spec[arm_id]), arm_id


def test_iex1h_a_gold_cost_control_keeps_running_and_iex15_keeps_its_own_retirement(spec) -> None:
    assert not spec["IEX1H"].retired and spec["IEX1H"].status == "run"
    assert spec["IEX15"].retired_day == "2026-11-13"


def test_the_gold_arms_and_their_matched_controls_keep_running(spec) -> None:
    running = {i for i, a in spec.items() if not a.retired and a.status == "run"}
    assert running == {"S0", "S0R", "S1A", "S1B", "Z1P", "Z2P", "Z5P", "Z3D", "Z9D", "Z10D", "Z3V", "Z5V", "WKS0",
                       "WKI0", "GS1", "GS1C", "LCZ3D", "LCZ9D", "LCZ10D", "LCZ1P", "LCZ2P", "LCZ5P", "VEX15", "VEX1H",
                       "IEX1H"}


def test_the_retired_non_gold_rows_stay_registered_so_open_positions_keep_every_exit(spec, instruments,
                                                                                     accounts) -> None:
    ruled = {i for i, a in spec.items() if _ruled(a)}
    registered: set[str] = set()
    for acc in accounts.values():
        cfg = exploration.config_for(acc)
        if cfg is None:
            continue
        running, refused = {a.arm_id for a in cfg.arms}, exploration.spec_retired(cfg)
        for arm_id in ruled & running:
            assert arm_id in refused, (acc.account_id, arm_id)
        registered |= ruled & set(acc.exploration.arms)
    assert registered, "the ruling's rows are still on their accounts"


# ------------------------------------------------------------------------------------------------ 2. no new 1m arm
def _write(tmp_path: Path, *rows: arms.Arm) -> Path:
    p = tmp_path / "arms.yaml"
    p.write_text(yaml.safe_dump({"arms": [r.model_dump(exclude={"entry_refused"}, exclude_defaults=True) for r in rows]}))
    return p


def _row(spec: dict[str, arms.Arm], base: str, new_id: str, **update: object) -> arms.Arm:
    a = spec[base].model_copy(update={"id": new_id, **update})
    return a.model_copy(update={"config_hash": arms.arm_hash(a)})


def test_a_new_1m_arm_is_refused_by_the_spec(tmp_path, spec) -> None:
    with pytest.raises(ValueError, match="1m"):
        arms.load_arms(_write(tmp_path, _row(spec, "S0", "S0NEW")))


def test_a_new_arm_on_5m_or_slower_still_loads(tmp_path, spec) -> None:
    [a] = arms.load_arms(_write(tmp_path, _row(spec, "S1A", "S1NEW")))
    assert a.timeframe == "5m"


def test_every_1m_arm_already_in_the_spec_keeps_loading_and_the_running_ones_keep_running(spec) -> None:
    one = {i for i, a in spec.items() if a.timeframe == "1m"}
    assert one <= arms.ONE_MINUTE_ALLOWED
    assert {i for i in one if not spec[i].retired} == {"S0", "S0R", "Z1P", "Z2P", "WKS0", "LCZ1P", "LCZ2P"}


def test_an_allowed_1m_id_loads_from_any_spec_file(tmp_path, spec) -> None:
    [a] = arms.load_arms(_write(tmp_path, spec["S0"]))
    assert a.id == "S0" and a.timeframe == "1m"


# ------------------------------------------------------------------------------------------------ 3. A2 and 1h+
@pytest.mark.parametrize("tf", ["1h", "4h", "1d", "1w"])
def test_an_a2_readability_retirement_of_a_1h_or_slower_arm_is_refused(tmp_path, spec, tf) -> None:
    row = _row(spec, "MB1H", f"MBX{tf}", timeframe=tf, retired_day="2026-11-14",
               retired_rule="A2 readability, 2026-11-13: 12 admissible fills, < 100")
    assert not arms.a2_readability_applies(row)
    with pytest.raises(ValueError, match="A2 readability"):
        arms.load_arms(_write(tmp_path, row))


@pytest.mark.parametrize("tf", ["5m", "15m", "30m"])
def test_an_a2_readability_retirement_still_applies_below_1h(tmp_path, spec, tf) -> None:
    row = _row(spec, "MB1H", f"MBX{tf}", timeframe=tf, retired_day="2026-11-14",
               retired_rule="A2 readability, 2026-11-13: 12 admissible fills, < 100")
    assert arms.a2_readability_applies(row)
    [a] = arms.load_arms(_write(tmp_path, row))
    assert a.retired


def test_the_a2_retirements_recorded_before_the_ruling_stand(spec) -> None:
    """"A retirement is never undone": MB1H, W1A, W1B, W2 and their twins keep their 2026-10-30 A2 rows."""
    for arm_id in ("MB1H", "VMB1H", "W1A", "W1B", "W2", "VW1A", "VW1B", "VW2"):
        assert spec[arm_id].retired_day == "2026-10-30" and spec[arm_id].retired_rule.startswith("A2 readability")


@pytest.mark.parametrize("rule", ["A2 readability, 2026-11-13: 12 fills", "kill rules (A2): < 100 fills by 11-13",
                                  "a2  readability 2026-11-14"])
def test_the_a2_guard_matches_a2_anywhere_in_the_rule_not_only_as_a_prefix(tmp_path, spec, rule) -> None:
    row = _row(spec, "MB1H", "MBX1H", retired_day="2026-11-14", retired_rule=rule)
    with pytest.raises(ValueError, match="A2"):
        arms.load_arms(_write(tmp_path, row))


def test_a_rule_naming_a_longer_id_that_starts_with_a2_is_not_an_a2_retirement(tmp_path, spec) -> None:
    row = _row(spec, "MB1H", "MBX1H", retired_day="2026-11-14", retired_rule="A25 history bound, 2026-11-14")
    [a] = arms.load_arms(_write(tmp_path, row))
    assert a.retired


# ------------------------------------------------------------------------------------------------ the engine path
def _stray_spec(tmp_path: Path, spec: dict[str, arms.Arm]) -> Path:
    """The real S0 and S0R, plus a stray new 1m row and a 1h row retired under A2 after the ruling."""
    return _write(tmp_path, spec["S0"], spec["S0R"], _row(spec, "S0R", "S0NEW"),
                  _row(spec, "MB1H", "MBX1H", retired_day="2026-11-14", retired_rule="A2 readability, 2026-11-13"))


def _account() -> acc_mod.Account:
    reg = {i: acc_mod.ExplorationArm(max_loss=6.5, style="scalp", control_share=0.2)
           for i in ("S0", "S0R", "S0NEW", "MBX1H")}
    return _probe_account(exploration=_explore(arms=reg, pots={"scalp": acc_mod.ExplorationPot(gbp=10, max_open=8)}))


def test_a_stray_row_never_aborts_the_engine_config_and_stays_configured_with_its_entries_refused(tmp_path,
                                                                                                    spec) -> None:
    path = _stray_spec(tmp_path, spec)
    with pytest.raises(ValueError, match="1m"):
        arms.load_arms(path)                                   # the research and CI loader still refuses the file
    cfg = exploration.config_for(_account(), spec_path=path, spec_arms_path=tmp_path / "none.yaml")
    assert cfg is not None and [a.arm_id for a in cfg.arms] == ["MBX1H", "S0", "S0NEW", "S0R"] and not cfg.dropped
    refused = exploration.entry_refused(cfg)
    assert set(refused) == {"S0NEW", "MBX1H"}
    assert "1m" in refused["S0NEW"] and "A2" in refused["MBX1H"]


def test_the_engine_starts_with_a_stray_row_journals_it_and_keeps_its_exits(tmp_path, spec) -> None:
    path = _stray_spec(tmp_path, spec)
    cfg = exploration.config_for(_account(), spec_path=path, spec_arms_path=tmp_path / "none.yaml")
    assert cfg is not None
    eng, _ = _engine(tmp_path, list(cfg.arms), qualify=False)
    assert eng.exploration is not None
    assert {"S0NEW", "MBX1H"} <= set(eng.exploration.retired) and "S0NEW" in eng.exploration.arms
    journal = {d["arm"]: d["rule"] for d in eng.decisions if d.get("action") == "exploration_arm_unqualified"}
    assert "1m" in journal["S0NEW"]                            # MBX1H is a spec retirement, journalled as one
    assert "S0" not in eng.exploration.retired


def test_a_discovered_1m_arm_is_left_out_of_the_engine_and_named(tmp_path, spec) -> None:
    spec_arms = _write(tmp_path, _row(spec, "S0R", "D1M"))
    found, rejected = exploration._load_spec_arm_rows(spec_arms, live_class=False)
    assert not found and rejected == ["D1M"]
