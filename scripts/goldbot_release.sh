#!/usr/bin/env bash
# goldbot-release: deploy one CI-green main commit to the brain, and nothing else (ROADMAP 5a FR-9;
# docs/process/deploy-approval-runbook.md option B, steps D2-D4).
#   goldbot_release.sh <40-hex sha>
# Refuses (exit 2) unless the SHA is a full 40-hex commit that is on GitHub's main (an ancestor of, or equal to,
# its tip) and whose latest backend and frontend GitHub Actions check runs succeeded (for a merge commit with its PR
# head's tree: the PR head's runs, as goldbot-deploy accepts). Then runs exactly these remote commands on goldbot-brain:
#   D2  sudo systemctl reset-failed 'goldbot-deploy-manual-*'; sudo systemd-run --wait --unit
#       goldbot-deploy-manual-$(date +%s) /usr/local/sbin/goldbot-deploy <sha>
#   D3  sudo tail -1 /var/lib/goldbot-deploy/deploys.jsonl       (again after the 90 s rollback watch)
#   D4  bash -lc "goldbot run health --json"                     (entry status, engines, reconcile = positions vs broker)
#       bash -lc "goldbot run positions --account <id>"          (each enabled account, as health's entry_status:<id>)
# Exit 0: deployed and the post-deploy checks pass; 3: the deploy record does not say deployed <sha> (refused,
# failed or rolled back: the box restored the previous commit); 4: deployed, but a post-deploy check fails.
# The remote command strings are fixed here; the only input is the validated SHA. No rollback, stage, MT5-box or
# other remote command is possible through this script (those stay per-command owner approvals). Installed by the
# owner outside the repo (~/.local/bin/goldbot-release) after review, checked by sha256.
set -euo pipefail
export LC_ALL=C

REPO=engraamirkhan/goldbot
ACTIONS_APP_ID=15368
WATCH_S=${GOLDBOT_RELEASE_WATCH_S:-95}   # the on-box watcher rolls back on a restart up to 90 s after the switch
SSH=(ssh -F "$HOME/.ssh/goldbot.conf" -o BatchMode=yes goldbot-brain)
say() { echo "$(date -u +%H:%M:%S) $*"; }
refuse() { say "REFUSED: $*"; exit 2; }

[ $# -eq 1 ] || refuse "usage: goldbot_release.sh <40-hex sha>"
SHA=$1
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || refuse "not a full lowercase 40-hex commit: ${SHA:0:60}"
case "$WATCH_S" in ''|*[!0-9]*) refuse "GOLDBOT_RELEASE_WATCH_S takes whole seconds" ;; esac

# --- preconditions, read-only GitHub API calls -------------------------------------------------------------------
status=$(gh api "repos/$REPO/compare/$SHA...main" --jq .status 2>/dev/null) || refuse "$SHA: not found on GitHub"
case "$status" in ahead|identical) ;; *) refuse "$SHA is not on main (compare: $status)" ;; esac

ci_green() {  # yes when the latest backend and frontend Actions check runs of $1 succeeded
  local n
  n=$(gh api "repos/$REPO/commits/$1/check-runs?per_page=100&filter=latest&app_id=$ACTIONS_APP_ID" \
      --jq '[.check_runs[] | select((.name == "backend" or .name == "frontend") and .conclusion == "success") | .name] | unique | length' \
      2>/dev/null) || n=0
  [ "$n" = 2 ]
}
tree_of() { gh api "repos/$REPO/git/commits/$1" --jq .tree.sha 2>/dev/null; }
second_parent() { gh api "repos/$REPO/commits/$1" --jq '.parents[1].sha // ""' 2>/dev/null; }

if ci_green "$SHA"; then
  say "CI green on $SHA"
else
  head=$(second_parent "$SHA") || head=""
  [[ "$head" =~ ^[0-9a-f]{40}$ ]] || refuse "no green backend + frontend CI run on $SHA"
  [ "$(tree_of "$SHA")" = "$(tree_of "$head")" ] || refuse "no green CI on $SHA and its tree differs from PR head $head"
  ci_green "$head" || refuse "no green backend + frontend CI run on $SHA or its PR head $head"
  say "CI green on PR head $head (same tree as $SHA)"
fi

# --- the fixed remote commands -------------------------------------------------------------------------------------
D2="sudo systemctl reset-failed 'goldbot-deploy-manual-*'; sudo systemd-run --wait --unit goldbot-deploy-manual-\$(date +%s) /usr/local/sbin/goldbot-deploy $SHA"
D3="sudo tail -1 /var/lib/goldbot-deploy/deploys.jsonl"
D4='bash -lc "goldbot run health --json"'
d4_positions() { echo "bash -lc \"goldbot run positions --account $1\""; }   # $1 checked against ^[a-z0-9-]+$

# deploys.jsonl's last record -> "deployed" / "deployed_degraded" / other; prints a one-line, printable summary
read_record() {
  python3 -I -c '
import json, sys
sha = sys.argv[1]
raw = sys.stdin.read()[-65536:]
clean = lambda s: "".join(c for c in str(s) if c.isprintable())[:300]
try:
    rec = json.loads(raw.strip().splitlines()[-1])
except (ValueError, IndexError):
    print("unreadable record: " + clean(raw)); sys.exit(1)
res, to = rec.get("result"), rec.get("to")
print("deploys.jsonl: " + clean(json.dumps(rec)))
sys.exit(0 if res in ("deployed", "deployed_degraded") and to == sha else 1)
' "$SHA"
}

started=$(date +%s)
say "deploy $SHA to the brain (D2)"
"${SSH[@]}" "$D2" || say "D2 exited non-zero: reading the record"
rec=$("${SSH[@]}" "$D3") || rec=""
printf '%s\n' "$rec" | read_record || { say "FAILED: the record does not say deployed $SHA"; exit 3; }

wait_s=$((WATCH_S - ($(date +%s) - started)))
if [ "$wait_s" -gt 0 ]; then say "waiting ${wait_s}s for the on-box rollback watch"; sleep "$wait_s"; fi
rec=$("${SSH[@]}" "$D3") || rec=""
printf '%s\n' "$rec" | read_record || { say "FAILED: rolled back or replaced after the switch"; exit 3; }

say "post-deploy checks (D4)"
health=$("${SSH[@]}" "$D4") || true   # exit 1 on any failing check: judged below, per check
printf '%s\n' "$health" | python3 -I -c '
import json, sys
clean = lambda s: "".join(c for c in str(s) if c.isprintable())[:200]
try:
    rep = json.loads(sys.stdin.read()[-4_000_000:])
    checks = rep["checks"]
except (ValueError, KeyError, TypeError):
    print("health: unreadable report"); sys.exit(1)
watched = ("entry_status:", "engine:", "reconcile:")
bad = 0
print("health overall: " + clean(rep.get("status")))
for c in checks:
    name, st = clean(c.get("name")), clean(c.get("status"))
    if name.startswith(watched) or st == "fail":
        print("  %-5s %s  %s" % (st, name, clean(c.get("reason", ""))))
    if st == "fail" and name.startswith(watched):
        bad += 1
if not any(clean(c.get("name")).startswith("entry_status:") for c in checks):
    print("  no entry_status check in the report"); bad += 1
sys.exit(1 if bad else 0)
' || { say "DEPLOYED $SHA, but a post-deploy check fails (entry status, engine or reconcile): look before trading on"; exit 4; }

# D4 positions, per enabled account: the accounts are the entry_status:<id> checks (health hides disabled accounts).
# An id read from remote output goes into a remote command only when it is a plain account id.
accounts=$(printf '%s\n' "$health" | python3 -I -c '
import json, re, sys
for c in json.loads(sys.stdin.read()[-4_000_000:])["checks"]:
    n = str(c.get("name", ""))
    if n.startswith("entry_status:") and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", n[13:]):
        print(n[13:])
')
bad=0
for acc in $accounts; do
  [[ "$acc" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || continue
  say "positions $acc (D4)"
  if out=$("${SSH[@]}" "$(d4_positions "$acc")"); then
    printf '%s\n' "$out" | LC_ALL=C tr -cd '\11\12\40-\176' | tail -n 50
  else
    printf '%s\n' "$out" | LC_ALL=C tr -cd '\11\12\40-\176' | tail -n 5
    say "positions for $acc could not be read"; bad=1
  fi
done
[ "$bad" -eq 0 ] || { say "DEPLOYED $SHA, but positions could not be read for every account: look before trading on"; exit 4; }
say "DEPLOYED $SHA: record deployed; entry status, engines and reconcile not failing; positions read per account"
