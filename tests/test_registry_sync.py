import json
import sys

import pytest

from goldbot.research import registry_sync as rs
from goldbot.research.registry import TrialRegistry


def _rows(tmp_path, name, specs):
    reg = TrialRegistry(tmp_path / name)
    for agent, cfg in specs:
        reg.record(agent_id=agent, family="session_open", config=cfg, feature_version="f1", rationale="t", results={})
    return rs.read_rows(tmp_path / name)


def test_merge_is_a_renumbered_union_and_idempotent(tmp_path):
    vps = _rows(tmp_path, "vps.jsonl", [("a", {"x": 1}), ("b", {"x": 2})])
    wf = _rows(tmp_path, "wf.jsonl", [("c", {"x": 3})])
    merged = rs.merge_rows(vps, wf)
    assert [r["trial"] for r in merged] == [1, 2, 3]
    assert sorted(r["agent_id"] for r in merged) == ["a", "b", "c"]
    assert rs.merge_rows(merged, vps, wf) == merged          # re-merging adds nothing
    assert rs.merge_rows(wf, vps) == merged                  # order of sources does not matter


def test_cli_merge_used_by_the_workflow(tmp_path, monkeypatch, capsys):
    _rows(tmp_path, "a.jsonl", [("a", {"x": 1})])
    _rows(tmp_path, "b.jsonl", [("b", {"x": 2})])
    monkeypatch.setattr(sys, "argv", ["registry_sync", "merge", str(tmp_path / "a.jsonl"), str(tmp_path / "b.jsonl"),
                                      "-o", str(tmp_path / "out.jsonl")])
    rs.main()
    assert "2 trials" in capsys.readouterr().out
    assert TrialRegistry(tmp_path / "out.jsonl").n_trials == 2


def test_sync_pulls_unions_and_pushes(tmp_path, monkeypatch):
    local = tmp_path / "local.jsonl"
    _rows(tmp_path, "local.jsonl", [("a", {"x": 1})])
    remote = _rows(tmp_path, "remote.jsonl", [("w", {"x": 9}), ("w", {"x": 10})])
    pushed: dict[str, list] = {}
    monkeypatch.setattr(rs, "pull_release", lambda token, repo=rs.REPO: remote)
    monkeypatch.setattr(rs, "push_release", lambda token, rows, repo=rs.REPO: pushed.setdefault("rows", rows))
    assert rs.sync(local, "tok") == 3
    assert TrialRegistry(local).n_trials == 3 and len(pushed["rows"]) == 3
    assert json.loads(local.read_text().splitlines()[-1])["trial"] == 3


def test_monthly_loop_survives_a_failed_sync(tmp_path):
    from goldbot.ops.jobs import _sync_trials

    class Ctx:
        trials = TrialRegistry(tmp_path / "t.jsonl")

        @staticmethod
        def sync_trials(path):
            raise OSError("network down")
    assert str(_sync_trials(Ctx())).startswith("failed: network down")   # type: ignore[arg-type]


@pytest.mark.parametrize("missing", [True, False])
def test_read_rows_tolerates_missing_and_blank_lines(tmp_path, missing):
    p = tmp_path / "r.jsonl"
    if not missing:
        p.write_text('{"ts": "1", "agent_id": "a"}\n\n')
    assert len(rs.read_rows(p)) == (0 if missing else 1)
