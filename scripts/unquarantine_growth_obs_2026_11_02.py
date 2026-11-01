"""One-off repair, growth sprint 2026-11-01: release the GS1/GS1C candidates quarantined only for their `growth` style.

Until the fix of 2026-11-02 (`exploration_data.CANDIDATE_STYLES`) every growth-pot candidate was written quarantined,
"style 'growth' not one of ('scalp', 'intraday', 'swing')". The nightly replay (`label_candidates`) skips quarantined
rows, so no refused GS1/GS1C leg was ever scored: quant requirement 5's shadow fills were missing from the read. This
clears the quarantine of the rows whose ONLY problem was that style (exactly REASON); a row with any other problem keeps
it. Rows are never deleted. Idempotent: a second run changes nothing.

    sudo -n -u goldbot /opt/goldbot/.venv/bin/python scripts/unquarantine_growth_obs_2026_11_02.py --state-dir /opt/goldbot/state
"""
from __future__ import annotations

import argparse
import sys

from goldbot.db import Database

ARMS = ("GS1", "GS1C")
REASON = "style 'growth' not one of ('scalp', 'intraday', 'swing')"


def repair(db: Database) -> int:
    """Rows released now."""
    with db.write() as conn:
        cur = conn.execute("UPDATE exploration_obs SET quarantine = NULL WHERE style = 'growth' AND quarantine = ? "
                           f"AND arm_id IN ({','.join('?' * len(ARMS))})", (REASON, *ARMS))
        return int(cur.rowcount)


def remaining(db: Database) -> int:
    """Growth rows still quarantined, for any other reason (left as they are; printed for the read's owner)."""
    with db.connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM exploration_obs WHERE style = 'growth' AND quarantine IS NOT NULL "
                           f"AND arm_id IN ({','.join('?' * len(ARMS))})", ARMS).fetchone()
        return int(row[0])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state-dir", default="state")
    a = ap.parse_args()
    db = Database.open(a.state_dir, "aux")
    n = repair(db)
    print(f"growth candidates released from quarantine: {n}")
    print(f"growth candidates still quarantined for another reason: {remaining(db)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
