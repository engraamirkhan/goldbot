"""Fail loudly before GitHub disables the research loop's schedule (docs/plans/continuous-research-loop.md).

GitHub disables scheduled workflows of a public repository after 60 days without repository activity. The loop's own
runs do not count as activity, a commit does. This reads the last commit's time (`git log -1 --format=%ct`, passed as
`--last-commit`) and exits 1 once it is more than 50 days old, so the workflow run fails (the owner is notified by
GitHub) while 10 days remain; any commit to main, or "Enable workflow" in the Actions tab, resets it. It needs
no token and no write scope."""
from __future__ import annotations

import argparse
import time

WARN_DAYS = 50          # GitHub's limit is 60 days


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--last-commit", type=int, required=True, help="unix time of the newest commit on main")
    ap.add_argument("--now", type=int, default=None, help="unix time now (tests)")
    args = ap.parse_args(argv)
    age = ((args.now if args.now is not None else int(time.time())) - args.last_commit) / 86400
    if age > WARN_DAYS:
        print(f"research loop keep-alive: the last commit is {age:.0f} days old; GitHub disables scheduled workflows "
              f"after 60 days without repository activity. Push a commit to main (or re-enable the workflow).")
        return 1
    print(f"research loop keep-alive: last commit {age:.0f} days ago (limit {WARN_DAYS})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
