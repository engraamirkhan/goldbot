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
    stamp = (7, "t1", 1, "sha256:x")
    monkeypatch.setattr(rs, "pull_stamped", lambda t, bootstrap=False: (remote, stamp))
    monkeypatch.setattr(rs, "push_release", lambda t, rows, *, expected, strict=True: pushed.setdefault(
        "rows", rows) if expected == stamp else pytest.fail("pushed without the pulled stamp"))
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


def _failed_and_repair(tmp_path):
    rows = _rows(tmp_path, "reg.jsonl", [("a", {"x": 1}), ("planned-b", {"x": 2.0}), ("c", {"x": 3})])
    failed = {**rows[1], "status": "failed", "feature_version": "unknown"}
    repaired = {**rows[1], "agent_id": "b", "config_hash": "h-b", "status": "screened",
                "repair": {"replaces": {k: failed[k] for k in rs.KEY_FIELDS}, "trial": failed["trial"]}}
    return [rows[0], failed, rows[2]], repaired


def test_a_repair_row_supersedes_the_failed_row_it_names_in_every_union_and_keeps_its_trial_number(tmp_path):
    published, repaired = _failed_and_repair(tmp_path)
    stale = list(published)                              # e.g. the VPS copy, pulled before the repair
    fixed = [published[0], repaired, published[2]]
    for merged in (rs.merge_rows(fixed, stale), rs.merge_rows(stale, fixed), rs.merge_rows(stale, [repaired])):
        assert [(r["trial"], r["agent_id"], r["status"]) for r in merged] == \
            [(1, "a", "evaluated"), (2, "b", "screened"), (3, "c", "evaluated")]


def test_a_repair_can_only_supersede_a_failed_row_never_a_real_trial(tmp_path):
    published, repaired = _failed_and_repair(tmp_path)
    rogue = {**repaired, "repair": {"replaces": {k: published[0][k] for k in rs.KEY_FIELDS}}}
    merged = rs.merge_rows(published, [rogue])
    assert len(merged) == 4 and [r["agent_id"] for r in merged].count("a") == 1   # nothing dropped, one trial added


@pytest.mark.parametrize("change", [{"ts": "2099-01-01T00:00:00+00:00"}, {"trial": 7}],
                         ids=["repair-row-elsewhere-in-time", "another-trial-number"])
def test_a_repair_that_does_not_take_the_failed_rows_own_slot_drops_nothing(tmp_path, change):
    published, repaired = _failed_and_repair(tmp_path)
    if "ts" in change:
        repaired = {**repaired, **change}           # would move the trial in the numbering
    else:
        repaired = {**repaired, "repair": {**repaired["repair"], **change}}
    merged = rs.merge_rows(published, [repaired])
    assert len(merged) == 4 and sum(r["status"] == "failed" for r in merged) == 1


# ---------------------------------------------------------------------------------------------- 403 from GitHub
def _http_error(code):
    import io
    import urllib.error
    return urllib.error.HTTPError("https://api.github.com/x", code, "Forbidden", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]


def test_a_403_on_a_write_names_the_missing_contents_write_permission(monkeypatch):
    """Measured on the brain 2026-10-31: the token reads the release (contents:read) but the upload POST was refused
    with a bare "HTTP Error 403: Forbidden", which does not say what to fix."""
    def refuse(req, timeout=None):
        raise _http_error(403)
    monkeypatch.setattr("urllib.request.urlopen", refuse)
    with pytest.raises(rs.RegistryForbidden, match=r"POST .*Contents: Read and write.*engraamirkhan/goldbot"):
        rs.HttpTransport("tok").upload({"upload_url": "https://uploads.github.com/x/assets{?name,label}"}, "n", b"")


def test_a_403_on_a_read_names_contents_read(monkeypatch):
    def refuse(req, timeout=None):
        raise _http_error(403)
    monkeypatch.setattr("urllib.request.urlopen", refuse)
    with pytest.raises(rs.RegistryForbidden, match=r"Contents: Read-only"):
        rs.HttpTransport("tok").release()


def test_a_404_still_means_no_release(monkeypatch):
    def missing(req, timeout=None):
        raise _http_error(404)
    monkeypatch.setattr("urllib.request.urlopen", missing)
    assert rs.HttpTransport("tok").release() is None
