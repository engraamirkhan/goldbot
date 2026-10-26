"""One-off repair, live 2026-10-26 (PD decision B): quarantine the exploration_obs row a broker-clock fault misfiled.

Before the 06:24 UTC clock fix, server_tz Europe/Athens (UTC+2 after EU DST) against the broker's NY+7 (UTC+3) stamped
I0's 06:00 bar 07:00. Its refused candidate took the id of the real 07:00 candidate, whose order filled at 07:00:08, so
the row says `blocked` for a trade that was taken (first record stands). The row is marked quarantined with REASON:
never deleted, and training reads no quarantined row (`exploration_data.read_obs`). Idempotent: a row already
quarantined, or absent, is left as it is. The engine fix (`exploration.step` defers a bar ahead of the host clock) keeps
it from happening again.

    sudo -n -u goldbot /opt/goldbot/.venv/bin/python scripts/repair_exploration_obs_2026_10_26.py --state-dir /opt/goldbot/state
"""
from __future__ import annotations

import argparse
import sys

from goldbot.db import Database

CANDIDATE_ID = "exa4a6f-1792998000-f5d1e8f3"
REASON = "clock_gap_week_misfiled_2026-10-26"


def repair(db: Database) -> int:
    """Rows quarantined now (0 or 1)."""
    with db.write() as conn:
        cur = conn.execute("UPDATE exploration_obs SET quarantine = ? WHERE candidate_id = ? AND quarantine IS NULL",
                           (REASON, CANDIDATE_ID))
        return int(cur.rowcount)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state-dir", default="state")
    a = ap.parse_args()
    n = repair(Database.open(a.state_dir, "aux"))
    print(f"{CANDIDATE_ID}: {'quarantined (' + REASON + ')' if n else 'unchanged (absent or already quarantined)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
