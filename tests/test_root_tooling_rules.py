"""Standing rule for root-run tooling (docs/AGENT_STANDARDS.md "Root-run tooling"): what root installs or executes
comes from a source outside the service user's control and is hash-checked off-box; the service user's git object
store is not a source (it can swap a blob with `git replace`); no runbook runs root chmod/chown/cp/rm/install/tee/mv
on a path under /opt/goldbot. Scans every runbook and the Linux ops scripts, comments included."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FILES = sorted([*REPO.glob("docs/**/*.md"), REPO / "README.md", *REPO.glob("goldbot/ops/linux/*.sh")])

# root (plain `sudo`, not `sudo -u <user>`) changing or copying something under the service user's tree
ROOT_ON_SERVICE_PATH = re.compile(r"\bsudo\s+(?:chmod|chown|cp|rm|mv|tee|install)\b[^\n|;&]*(?:/opt/goldbot|<repo>)")
# root consuming what the service user's git prints (git show/cat-file/archive ... | sudo ...)
ROOT_FROM_SERVICE_GIT = re.compile(r"git\b[^\n|]*\b(?:show|cat-file|archive)\b[^\n|]*\|\s*sudo\b(?!\s+-u)")


def _hits(pattern: re.Pattern[str]) -> list[str]:
    out = []
    for f in FILES:
        if not f.exists():
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                out.append(f"{f.relative_to(REPO)}:{n}: {line.strip()}")
    return out


def test_no_runbook_runs_root_file_commands_on_service_paths():
    assert _hits(ROOT_ON_SERVICE_PATH) == []


def test_root_never_installs_from_the_service_users_git():
    assert _hits(ROOT_FROM_SERVICE_GIT) == []


def test_the_patterns_catch_what_they_are_for():
    assert ROOT_ON_SERVICE_PATH.search("sudo chmod 600 /opt/goldbot/config/settings.local.yaml")
    assert ROOT_ON_SERVICE_PATH.search("sudo install -m 755 -o root -g root <repo>/goldbot/ops/linux/goldbot-deploy.sh /x")
    assert not ROOT_ON_SERVICE_PATH.search("sudo -u goldbot chmod 600 /opt/goldbot/config/settings.local.yaml")
    assert ROOT_FROM_SERVICE_GIT.search("sudo -u goldbot -H git -C /opt/goldbot show <SHA>:x.sh | sudo tee /usr/local/x")
    assert not ROOT_FROM_SERVICE_GIT.search("git show <SHA>:x.sh | shasum -a 256")
