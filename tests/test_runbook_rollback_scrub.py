"""docs/RUNBOOK.md, "After a rollback below 04f6266": the scrub block runs as printed (against a temporary state
directory) and removes only the decider identity an older release wrote into approvals/done (BACKLOG 52 re-review)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from goldbot.telegram.approvals import Outcome, Proposal

REPO = Path(__file__).resolve().parents[1]
DONE = "/opt/goldbot/state/approvals/done"


def _scrub_block() -> str:
    text = (REPO / "docs" / "RUNBOOK.md").read_text(encoding="utf-8")
    section = text[text.index("After a rollback below `04f6266`"):]
    m = re.search(r"<<'PY'\n(.*?)\n\s*PY\n", section, re.S)
    assert m, "the scrub block is missing from the RUNBOOK's rollback section"
    lines = m.group(1).splitlines()
    indent = min(len(ln) - len(ln.lstrip()) for ln in lines if ln.strip())
    return "\n".join(ln[indent:] for ln in lines)


def _done(path: Path, **extra: object) -> Path:
    p = Proposal(proposal_id=path.stem, account_id="icm-demo", agent_id="a", side=1, lots=0.1, entry=2400.0,
                 stop=2390.0, target=2420.0, p=0.6, ev_r=0.2, spread_points=20, top_features=[], created=1.0,
                 outcome=Outcome.APPROVED, decided_ts=2.0)
    d = json.loads(p.model_dump_json())
    d.pop("decided_channel")
    d.update(extra)
    path.write_text(json.dumps(d))
    os.utime(path, (1_700_000_000, 1_700_000_000))
    return path


def test_runbook_scrub_drops_only_the_identity_and_keeps_channel_outcome_and_mtime(tmp_path):
    done = tmp_path / "done"
    done.mkdir()
    dash = _done(done / "p1.json", decided_via="dashboard:o@x.io")
    tg = _done(done / "p2.json", decided_by=111, decided_via="telegram:111")
    bot = _done(done / "p3.json", decided_by=111)
    clean = _done(done / "p4.json", decided_channel="telegram")
    before = clean.read_bytes()
    code = _scrub_block().replace(DONE, str(done))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.split() == ["scrubbed", "p1.json", "scrubbed", "p2.json", "scrubbed", "p3.json"]
    for f, channel in ((dash, "dashboard"), (tg, "telegram"), (bot, "telegram")):
        d = json.loads(f.read_text())
        assert not {"decided_by", "decided_via"} & d.keys()
        assert d["decided_channel"] == channel and d["outcome"] == "APPROVED" and d["decided_ts"] == 2.0
        assert f.stat().st_mtime == 1_700_000_000           # the dashboard's recent list and the digest read it
        assert "111" not in f.read_text() and "o@x.io" not in f.read_text()
    assert clean.read_bytes() == before                     # a file without identity is not rewritten


def test_runbook_grep_names_the_done_directory_the_scrub_reads():
    text = (REPO / "docs" / "RUNBOOK.md").read_text(encoding="utf-8")
    assert f"""sudo -u goldbot grep -rlE '"decided_(by|via)"' --include='*.json' {DONE}""" in text
    assert f'Path("{DONE}")' in _scrub_block()
