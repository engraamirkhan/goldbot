"""Performance report (markdown + JSON) for the performance-analyst and finance-auditor agents, from a state folder:
the brain's own `state/` or a copy on the Mac. Read only. The same as `python -m goldbot.ops.run perf-report`; see
goldbot/ops/perf_report.py for what it reports and how.

    python scripts/performance_report.py --state ~/goldbot-state-copy [--data-root DIR] --from 2026-10-01 --to 2026-10-31
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.ops.perf_report import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
