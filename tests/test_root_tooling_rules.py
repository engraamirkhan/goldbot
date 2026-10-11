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


# --- root-run shell scripts (goldbot/ops/linux/*.sh): a file command or redirection on a service-user path must run
# as that user. Allowed prefixes: `sudo -u`, `as_owner` (goldbot-deploy's sudo -u wrapper). A line that must run as
# root carries `# root-ok: <reason>` (for example creating /opt/goldbot before the service user owns anything).
SERVICE_PATH = r"(?:\$\{?ROOT\}?|/opt/goldbot|/home/mt5|/var/lib/goldbot(?![-\w]))"
# the command word in command position (segment start, after ( { then do or a plain sudo), or any redirection
ROOT_FILE_OP = re.compile(r"(?:(?:^|[({]|\bthen\b|\bdo\b|\bsudo\b)\s*(?:chmod|chown|cp|mv|rm|install|tee|cat)\b|>>?)"
                          r".*?" + SERVICE_PATH)
ALLOWED_PREFIX = re.compile(r"^\s*(?:sudo\s+-u\b|as_owner\b)")
ROOT_OK = re.compile(r"^#\s*root-ok:\s*\S")


def _split(line: str) -> tuple[list[str], str]:
    """(command segments split on && || ; | outside quotes, trailing comment outside quotes or "")."""
    segs, cur, quote, i = [], "", "", 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < len(line):
                cur += line[i:i + 2]
                i += 2
                continue
            if ch == quote:
                quote = ""
            cur += ch
        elif ch in "'\"":
            quote = ch
            cur += ch
        elif ch == "\\" and i + 1 < len(line):
            cur += line[i:i + 2]
            i += 2
            continue
        elif ch == "#" and (not cur or cur[-1].isspace()) and not cur.rstrip().endswith("$"):
            segs.append(cur)
            return [x for x in segs if x.strip()], line[i:]
        elif line.startswith(("&&", "||"), i):
            segs.append(cur)
            cur = ""
            i += 2
            continue
        elif ch in ";|":
            segs.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    segs.append(cur)
    return [x for x in segs if x.strip()], ""


def _shell_hits(text: str, name: str) -> list[str]:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        segs, comment = _split(line)
        if ROOT_OK.search(comment):
            continue
        if any(ROOT_FILE_OP.search(seg) and not ALLOWED_PREFIX.search(seg) for seg in segs):
            out.append(f"{name}:{n}: {line.strip()}")
    return out


def test_root_scripts_touch_service_paths_only_as_the_service_user():
    hits = []
    for f in sorted(REPO.glob("goldbot/ops/linux/*.sh")):
        hits += _shell_hits(f.read_text(encoding="utf-8"), str(f.relative_to(REPO)))
    assert hits == []


def test_the_shell_rule_catches_the_patterns_the_review_found():
    old = """install -d -m 700 -o mt5 -g mt5 /home/mt5/.ssh
echo "restrict,port-forwarding $key" >> /home/mt5/.ssh/authorized_keys
chown mt5:mt5 /home/mt5/.ssh/authorized_keys && chmod 600 /home/mt5/.ssh/authorized_keys
mkdir -p "$ROOT/state" "$ROOT/logs" && chown -R mt5:mt5 "$ROOT" && chmod 700 "$ROOT/state"
chmod 700 "$ROOT/state"
cat /var/lib/goldbot/.ssh/id_ed25519.pub
"""
    assert len(_shell_hits(old, "old")) == 6
    ok = """sudo -u mt5 -H sh -c 'umask 077; mkdir -p ~/.ssh && printf "%s\\n" "$1" >> ~/.ssh/authorized_keys' _ "$line"
sudo -u goldbot chmod 700 "$ROOT/state"
  as_owner rm -f -- "$ROOT/state/x"
install -m 755 -o root -g root "$OPS/goldbot-deploy.sh" /usr/local/sbin/goldbot-deploy
install -d -m 700 -o root -g root /var/lib/goldbot-deploy
"""
    assert _shell_hits(ok, "ok") == []
    assert _shell_hits("  wine python -m pip install --no-deps -e 'Z:$ROOT'", "pip") == []


def test_the_shell_rule_judges_each_command_segment():
    # an allowed prefix exempts only its own segment, never what follows && || ; or a pipe
    assert len(_shell_hits("sudo -u goldbot true && chmod 700 $ROOT/state", "x")) == 1
    assert len(_shell_hits("as_owner test -f x || rm -f /opt/goldbot/state/y", "x")) == 1
    assert len(_shell_hits("sudo -u goldbot true; cat /var/lib/goldbot/.ssh/id_ed25519.pub", "x")) == 1
    assert len(_shell_hits("sudo -u goldbot cat x | tee $ROOT/state/y", "x")) == 1
    # a separator inside quotes belongs to the quoted command (here: run as the service user)
    assert _shell_hits("sudo -u mt5 sh -c 'umask 077; chmod 700 /home/mt5/.ssh && echo ok >> /home/mt5/x' _", "x") == []


def test_root_ok_counts_only_as_a_trailing_comment_outside_quotes():
    assert _shell_hits('install -d -m 755 -o mt5 -g mt5 "$ROOT"   # root-ok: creates /opt/goldbot', "x") == []
    assert len(_shell_hits('echo "# root-ok: x"; chmod 700 $ROOT/state', "x")) == 1
    assert len(_shell_hits("chmod 700 $ROOT/state '# root-ok: inside quotes'", "x")) == 1
    assert len(_shell_hits("chmod 700 $ROOT/state  # root-ok:", "x")) == 1          # a reason is required
