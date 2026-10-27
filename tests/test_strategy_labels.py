"""config/strategy_labels.yaml (docs/design/trades-ux-2026-10-27.md risk 5: "labels drift from arm ids as arms are
added"): every running arm, every arm an account registers and every specialist family has the owner's name, and two
arms sharing a family and a timeframe have distinct variant tags. Fails CI on a new arm or family without a label.
config/arm_reads.yaml: an arm is read only once listed; a broken file reads no arm (fail closed)."""
from __future__ import annotations

import ast
from pathlib import Path

import yaml

from goldbot.api.strategy_labels import LABELS_PATH, READS_PATH, arm_family, load_catalog
from goldbot.engine.exploration import SPEC_PATH
from goldbot.ops.accounts import load_accounts
from goldbot.research.arms import load_arms

SPECIALISTS = Path(__file__).parents[1] / "goldbot" / "specialists"


def _specialist_families() -> set[str]:
    out = set()
    for f in SPECIALISTS.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and \
                    isinstance(node.value.value, str) and any(
                        isinstance(t, ast.Name) and t.id in ("family", "FAMILY") for t in node.targets):
                out.add(node.value.value)
    return out


def test_every_running_and_registered_arm_and_every_specialist_family_has_a_label():
    cat = load_catalog()
    spec = load_arms(SPEC_PATH, check_hash=False)
    families = {arm_family(a.rule, a.params) for a in spec} | _specialist_families()
    missing = sorted(f for f in families if f not in cat.labels.families)
    assert not missing, f"add these families to {LABELS_PATH.name}: {missing}"
    registered = {arm for acc in load_accounts(logins=False).values() for arm in acc.exploration.arms}
    assert registered <= set(cat.arms), sorted(registered - set(cat.arms))


def test_arms_sharing_a_family_and_timeframe_on_one_account_have_distinct_variant_tags():
    """The owner reads a row as account · strategy · variant: on one account no two running arms may read the same."""
    cat = load_catalog()
    spec = {a.id: a for a in load_arms(SPEC_PATH, check_hash=False) if a.status == "run"}
    for acc in load_accounts(logins=False).values():
        groups: dict[tuple[str, str], list[str]] = {}
        for arm in acc.exploration.arms:
            if arm in spec:
                groups.setdefault((cat.arms[arm].family, spec[arm].timeframe), []).append(arm)
        for key, ids in groups.items():
            variants = [cat.arms[i].variant for i in ids]
            assert len(set(variants)) == len(ids), f"{acc.account_id} {key}: give {ids} distinct tags"


def test_controls_are_the_random_rules_and_the_arms_registered_with_a_control_share():
    cat = load_catalog()
    for arm in ("S0", "S0R", "I0", "W0", "VI0", "VW0", "F1C", "G1C", "C1H"):
        assert cat.arms[arm].is_control, arm
    assert not cat.arms["S1A"].is_control and not cat.arms["G1A"].is_control
    assert cat.arms["XBB15"].family == "box_break"                    # a specialist arm: its specialist's family


def test_no_arm_is_read_until_arm_reads_lists_it_and_a_broken_file_reads_none(tmp_path):
    assert load_catalog().reads == {}                                 # as committed: no read recorded yet
    ok = tmp_path / "ok.yaml"
    ok.write_text('reads: {S1A: {read_utc: "2026-11-20T00:00:00Z", verdict: "no edge after costs"}}')
    assert load_catalog(reads_path=ok).is_read("S1A") and not load_catalog(reads_path=ok).is_read("S1B")
    bad = tmp_path / "bad.yaml"
    bad.write_text("reads: {S1A: {verdict: no time}}")
    assert load_catalog(reads_path=bad).reads == {}


def test_each_strategy_arm_is_read_against_its_named_or_own_family_control_else_its_sleeves_random_control():
    """Item 132: the board's control pairing (strategy_labels docstring), on the shipped config."""
    arms = load_catalog().arms
    pairs = {a: arms[a].control_arm for a in ("F1A", "KA15", "G1A", "C30A", "S1A", "I2A", "W1A", "Z3D", "PDC4", "VI2A")}
    assert pairs == {"F1A": "F1C", "KA15": "XC15", "G1A": "G1C", "C30A": "C30C", "S1A": "S0", "I2A": "I0",
                     "W1A": "W0", "Z3D": "ZCD", "PDC4": "W0", "VI2A": "VI0"}
    assert all(i.control_arm is None for i in arms.values() if i.is_control)


def test_pooled_families_and_read_sizes_come_from_the_evaluators_arm_reads_file(tmp_path):
    """Quant 2026-10-27: the read unit and n are the evaluator's (config/arm_reads.yaml `read_at`, `read_units`)."""
    cat = load_catalog()
    assert cat.unit_of("Z6D") == cat.unit_of("Z6V") == "H-81:Z6" and cat.read_size("Z6D") == 300
    assert cat.unit_of("BXT15") == "H-80:BXR" and not cat.arms["BXT15"].counted and cat.arms["BXR15"].counted
    assert cat.unit_of("ZCD") == "ZCD" and cat.read_size("ZCD") is None and cat.read_size("WFRI") == 170
    assert cat.arms["S0R"].count == "stop_exits" and cat.read_size("S0R") == 100
    spec = {a.id for a in load_arms(SPEC_PATH, check_hash=False)}
    raw = yaml.safe_load(READS_PATH.read_text())
    assert set(raw["read_at"]) <= spec and all(set(u["arms"]) <= spec for u in raw["read_units"].values())
    reads = tmp_path / "reads.yaml"
    reads.write_text(READS_PATH.read_text().replace("reads: {}", "reads: {'H-81:Z6': {read_utc: '2026-10-20T00:00:00Z', verdict: no edge}}"))
    assert load_catalog(reads_path=reads).is_read("Z6V")


def test_a_broken_reads_file_reads_no_unit_and_pools_nothing(tmp_path):
    bad = tmp_path / "reads.yaml"
    bad.write_text("reads: {'H-81:Z6': {read_utc: '2026-10-20T00:00:00Z', verdict: no edge}}\nread_at: {Z6D: 0}\n")
    cat = load_catalog(reads_path=bad)
    assert cat.reads == {} and cat.unit_of("Z6D") == "Z6D" and not cat.is_read("Z6D") and cat.read_size("Z6D") == 300
