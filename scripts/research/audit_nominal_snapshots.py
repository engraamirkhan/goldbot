"""One-off audit of nominal-pit feature snapshots for the self-retrain (quant S5, PR #434).

Counts the admitted nominal snapshots (decided on/after self_retrain.NOMINAL_FROM) whose decision read any minute
without a write stamp in [decision - 24 h, decision], and writes their candidate ids (the ones the trainer excludes; the
weekly job runs the same audit in memory every run). Read-only on aux.db and the store.

  uv run scripts/research/audit_nominal_snapshots.py --state-dir state [--data-root DIR] [--out ~/tmp/excluded.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--data-root", default=None, help="the store root (default: settings data_root)")
    ap.add_argument("--out", default=str(Path("~/tmp/self_retrain_nominal_excluded.json").expanduser()))
    a = ap.parse_args()
    from goldbot.config import load_settings
    from goldbot.data.store import Store
    from goldbot.db.connection import Database
    from goldbot.research.self_retrain import NOMINAL_FROM
    from goldbot.research.snapshot_audit import AUDIT_WINDOW, audit
    db = Database.open(Path(a.state_dir), "aux", migrate=False)
    got = audit(db, Store(a.data_root or load_settings().data_root), since=NOMINAL_FROM)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"since": NOMINAL_FROM.isoformat(), "window_h": AUDIT_WINDOW.total_seconds() / 3600,
                               "counts": got["counts"], "excluded": sorted(got["excluded"]),
                               "unread": sorted(got["unread"])}, indent=1))
    print(json.dumps(got["counts"]), "->", out)


if __name__ == "__main__":
    main()
