"""BACKLOG 216: the research director (the Mac Claude session; the brain's research_director job is off without an
Anthropic key) reads the brain's unread weekly roll-ups and marks each consumed with a recorded decision.

goldbot/research/weekly_inbox.py runs on the brain as the goldbot user (`list` reads only; `mark` records through
daily_review.mark_weekly_consumed, decision text on stdin). scripts/weekly_proposals.sh is the Mac side: it runs those
over ssh, validates the roll-up name before anything leaves the Mac, and never puts the decision on a command line.
The feedback-loop check (goldbot/ops/checks/loop.py) accepts any recorded consumer: marked roll-ups pass."""
from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from goldbot.ops.checks import loop
from goldbot.research import daily_review as dr
from goldbot.research import weekly_inbox as wi
from goldbot.research.daily_review import VariantProposal, WeeklyRollup, weekly_path
from tests.test_health import make_ctx, write

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "weekly_proposals.sh"
NOW = pd.Timestamp("2026-11-14 10:00", tz="UTC")


def _rollup(tmp: Path, day: str, n: int, age_d: float = 10) -> str:
    week = WeeklyRollup(week_ending=day, generated_utc=NOW - pd.Timedelta(days=age_d),
                        proposals=[VariantProposal(title=f"variant {i}", rationale=f"why {i}") for i in range(n)])
    write(weekly_path(tmp, day), json.loads(week.model_dump_json()))
    return weekly_path(tmp, day).name


def test_unread_lists_roll_ups_with_proposals_not_yet_consumed(tmp_path):
    a = _rollup(tmp_path, "2026-11-01", 2)
    _rollup(tmp_path, "2026-11-08", 0)                   # nothing proposed: nothing to read
    c = _rollup(tmp_path, "2026-10-25", 1)
    dr.mark_weekly_consumed(tmp_path, c, by="weekly_review", now=NOW)
    (dr.review_root(tmp_path) / "weekly-2026-10-18.json").write_text("{")
    items, bad = wi.unread(tmp_path)
    assert [i.name for i in items] == [a] and len(items[0].proposals) == 2
    assert bad == ["weekly-2026-10-18.json"]


def test_mark_records_the_decision_once_and_the_loop_check_then_passes(tmp_path):
    name = _rollup(tmp_path, "2026-11-01", 2)
    write(tmp_path / "scheduler.json", {"ts": NOW.isoformat(), "jobs": {}})
    assert loop._proposals(tmp_path, NOW).verdict == "fail"
    assert wi.mark(tmp_path, name, decision="variant 0 queued for pre-registration; variant 1 declined",
                   by="research_director", now=NOW)
    rec = dr.weekly_consumed(tmp_path)[name]
    assert rec["by"] == "research_director" and rec["decision"].startswith("variant 0 queued")
    assert not wi.mark(tmp_path, name, decision="again", by="research_director", now=NOW + pd.Timedelta("1h"))
    assert dr.weekly_consumed(tmp_path)[name]["decision"].startswith("variant 0 queued")   # the first record stands
    assert loop._proposals(tmp_path, NOW).verdict == "ok"
    assert "proposals: every weekly proposal read" in loop.check_feedback_loop(make_ctx(tmp_path, now=NOW)).reason


@pytest.mark.parametrize("name", ["weekly-2026-11-01.json; rm -rf /", "../consumed.json", "2026-11-01.json"])
def test_mark_refuses_a_bad_name(tmp_path, name):
    with pytest.raises(ValueError):
        wi.mark(tmp_path, name, decision="d", by="research_director", now=NOW)


def test_mark_refuses_an_unreadable_or_missing_roll_up_and_an_empty_decision(tmp_path):
    (dr.review_root(tmp_path)).mkdir(parents=True)
    (dr.review_root(tmp_path) / "weekly-2026-11-01.json").write_text("{")
    with pytest.raises(ValueError):
        wi.mark(tmp_path, "weekly-2026-11-01.json", decision="d", by="research_director", now=NOW)
    with pytest.raises(ValueError):
        wi.mark(tmp_path, "weekly-2026-11-08.json", decision="d", by="research_director", now=NOW)
    name = _rollup(tmp_path, "2026-10-25", 1)
    with pytest.raises(ValueError):
        wi.mark(tmp_path, name, decision="   ", by="research_director", now=NOW)
    assert dr.weekly_consumed(tmp_path) == {}


def test_cli_list_and_mark(tmp_path, capsys, monkeypatch):
    name = _rollup(tmp_path, "2026-11-01", 1)
    assert wi.main(["list", "--state-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert name in out and "variant 0" in out and "why 0" in out
    monkeypatch.setattr("sys.stdin", io.StringIO("declined: duplicates H-14\n"))
    assert wi.main(["mark", name, "--state-dir", str(tmp_path), "--by", "research_director"]) == 0
    assert dr.weekly_consumed(tmp_path)[name]["decision"] == "declined: duplicates H-14"
    assert wi.main(["list", "--state-dir", str(tmp_path)]) == 0
    assert "no unread" in capsys.readouterr().out


def test_cli_list_exits_non_zero_when_consumed_json_is_unreadable(tmp_path):
    _rollup(tmp_path, "2026-11-01", 1)
    (dr.review_root(tmp_path) / "consumed.json").write_text("[")
    assert wi.main(["list", "--state-dir", str(tmp_path)]) != 0


# ---------------------------------------------------------------------------------------------- the Mac script
def _fake_ssh(tmp: Path) -> dict[str, str]:
    b = tmp / "bin"
    b.mkdir()
    (b / "ssh").write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {tmp}/ssh.args\ncat > {tmp}/ssh.stdin\necho remote-ok\n')
    (b / "ssh").chmod(0o755)
    home = tmp / "home"
    home.mkdir()
    return {"PATH": f"{b}:/usr/bin:/bin", "HOME": str(home)}


def test_script_lists_read_only_as_the_goldbot_user(tmp_path):
    env = _fake_ssh(tmp_path)
    out = subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert out.returncode == 0, out.stderr
    remote = (tmp_path / "ssh.args").read_text().splitlines()[-1]
    assert "sudo -n -u goldbot" in remote and "goldbot.research.weekly_inbox list" in remote and "mark" not in remote
    assert "BatchMode=yes" in (tmp_path / "ssh.args").read_text()


def test_script_marks_with_the_decision_on_stdin_never_on_the_command_line(tmp_path):
    env = _fake_ssh(tmp_path)
    decision = "declined; $(touch pwned) `id` 'quoted'"
    out = subprocess.run(["/bin/bash", str(SCRIPT), "--mark", "weekly-2026-11-01.json", decision], env=env,
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert out.returncode == 0, out.stderr
    remote = (tmp_path / "ssh.args").read_text().splitlines()[-1]
    assert "weekly_inbox mark weekly-2026-11-01.json" in remote and "--by research_director" in remote
    assert "pwned" not in remote and (tmp_path / "ssh.stdin").read_text() == decision


@pytest.mark.parametrize("name", ["weekly-2026-11-01.json;id", "../x.json", ""])
def test_script_refuses_a_bad_name_before_any_ssh(tmp_path, name):
    env = _fake_ssh(tmp_path)
    out = subprocess.run(["/bin/bash", str(SCRIPT), "--mark", name, "d"], env=env, capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    assert out.returncode != 0 and not (tmp_path / "ssh.args").exists()


def test_script_is_executable():
    assert os.access(SCRIPT, os.X_OK)
