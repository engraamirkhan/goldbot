"""Service entry points used by NSSM on the VPS and by hand in development.

  python -m goldbot.ops.run supervisor
  python -m goldbot.ops.run engine <account_id>
  python -m goldbot.ops.run api
  python -m goldbot.ops.run webhook
  python -m goldbot.ops.run scheduler
  python -m goldbot.ops.run telegram
  python -m goldbot.ops.run news
  python -m goldbot.ops.run record-gate <gate_name> --evidence <path or text>   # appends to state/phase_state.json
  python -m goldbot.ops.run gates [--json] [--no-write]   # each roadmap gate met / not met + the stop rule (records nothing)
  python -m goldbot.ops.run gate-evidence <name> (--value X | --passed yes|no) [--detail TEXT]   # leakage audit, chaos drill
  python -m goldbot.ops.run health [--json] [--static] [--out FILE] [--baseline FILE]   (exit 1 on a fail)
  python -m goldbot.ops.run publish-costs [--out FILE]   # canonical broker's measured costs -> release costs-v1
  python -m goldbot.ops.run backup [--init]              # encrypted restic backup now (--init: create the repository once)
  python -m goldbot.ops.run restore --latest --to DIR    # restore the latest backup into an empty DIR and verify it
  python -m goldbot.ops.run restore-drill                # the weekly restore drill, now
  python -m goldbot.ops.run setup [--account ID]         # guided setup of secrets and server settings (re-runnable)
  python -m goldbot.ops.run preflight [--account ID]     # read-only checklist before starting services (exit 1 on a blocker)
  python -m goldbot.ops.run ruin --trades-per-week N (--closed-trades state | --shadow state | --r-file F | --win-rate ..)
                                                         # drawdown / risk-of-ruin Monte Carlo at the live limits (G-5)
  python -m goldbot.ops.run sizing-feasibility [--equity E] [--tiny-live] [--price P --stop-distance D]
                                                         # minimum equity for the minimum lot per family (G-7)
  python -m goldbot.ops.run revoke-sessions [--state DIR]  # sign every dashboard account out (a stolen session)
  python -m goldbot.ops.run mail-test                    # one test email to auth.owner_email (email sign-in setup)
  python -m goldbot.ops.run monthly-report [--state DIR] [--month YYYY-MM] [--json]
                                                         # time-weighted % return, R, drawdown, avg per day, milestones
  python -m goldbot.ops.run owner-history [PATH]         # the owner's MT5 history -> private report + research seeds
  python -m goldbot.ops.run perf-report [--state DIR] [--data-root DIR] [--from D] [--to D] [--out DIR] [--json]
                                                         # performance report (markdown + JSON) for the analysts; read only
"""
# The services live in goldbot/ops/services/ and the commands in goldbot/ops/commands/ (registry.py), one module each,
# so lanes stop colliding on this file. This module stays the public surface: it re-exports every name it always had,
# so imports and patches here (`patch("goldbot.ops.run.engine_pass")`) keep working; the moved code reaches moved names
# through it at call time (goldbot/ops/services/_base.py). It loads no third-party package at import (the MT5 bridge).
from __future__ import annotations

import logging
import sys
import time  # noqa: F401 - part of the surface: tests patch goldbot.ops.run.time.sleep

import goldbot.ops.commands  # noqa: F401 - registers every command
from goldbot.ops.commands.costs import costs_uploader, publish_costs_cli
from goldbot.ops.commands.drift import drift_review_cli
from goldbot.ops.commands.gates import record_gate_cli
from goldbot.ops.commands.registry import find
from goldbot.ops.services._base import log
from goldbot.ops.services.api import run_api
from goldbot.ops.services.bridge import run_bridge
from goldbot.ops.services.engine import (
    SESSIONS_TIMEOUT_S,
    SessionRefresher,
    _record_tick_error,
    _run_engine,
    _set_bridge_verdict,
    apply_bridge_compat,
    begin_bridge_check,
    engine_limits,
    engine_pass,
    expire_bridge_compat,
    paper_stand_in,
    refresh_sessions,
    refuse_start,
    run_engine,
    start_loop_watch,
)
from goldbot.ops.services.news import run_news
from goldbot.ops.services.scheduler import _agent_runner, _job_broker, run_scheduler
from goldbot.ops.services.supervisor import _SUP_ERR_LOGGED, SUP_ERR_LOG_S, run_supervisor, supervisor_pass
from goldbot.ops.services.telegram import run_telegram
from goldbot.ops.services.webhook import run_webhook

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
# httpx logs every request URL at INFO, and a Telegram Bot API URL carries the bot token (/bot<token>/getUpdates),
# so at INFO the token would reach journald on every poll. Its warnings and errors still log.
logging.getLogger("httpx").setLevel(logging.WARNING)

# every name this module had before the split (tests/test_ops_run_registry.py keeps the list whole)
__all__ = [
    "SESSIONS_TIMEOUT_S",
    "SUP_ERR_LOG_S",
    "SessionRefresher",
    "_SUP_ERR_LOGGED",
    "_agent_runner",
    "_job_broker",
    "_record_tick_error",
    "_run_engine",
    "_set_bridge_verdict",
    "apply_bridge_compat",
    "begin_bridge_check",
    "costs_uploader",
    "drift_review_cli",
    "engine_limits",
    "engine_pass",
    "expire_bridge_compat",
    "log",
    "main",
    "paper_stand_in",
    "publish_costs_cli",
    "record_gate_cli",
    "refresh_sessions",
    "refuse_start",
    "run_api",
    "run_bridge",
    "run_engine",
    "run_news",
    "run_scheduler",
    "run_supervisor",
    "run_telegram",
    "run_webhook",
    "start_loop_watch",
    "supervisor_pass",
]


def main(argv: list[str]) -> None:
    """Dispatch `argv` (sys.argv) through the command registry; no command or an unknown one prints this module's
    usage and exits 1."""
    cmd = argv[1] if len(argv) > 1 else ""
    spec = find(cmd)
    if spec is None:
        print(__doc__)
        sys.exit(1)
    spec.handler(cmd, argv[2:])


if __name__ == "__main__":
    main(sys.argv)
