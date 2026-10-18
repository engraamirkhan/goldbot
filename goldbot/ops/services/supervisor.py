"""The supervisor service (`python -m goldbot.ops.run supervisor`): evaluates the account-wide halts every 5 s.

Moved verbatim from goldbot/ops/run.py, which re-exports every name; calls between moved names and the rate-limit
globals go through goldbot.ops.run (`resolve`, `patched`: goldbot/ops/services/_base.py) so patching there still
takes effect.
"""
from __future__ import annotations

import time
from typing import Any

from goldbot.ops.services._base import log, patched, resolve

SUP_ERR_LOG_S = 300.0                 # a supervisor evaluate error's traceback is logged at most this often per type
_SUP_ERR_LOGGED: dict[str, float] = {}  # exception type -> monotonic time its traceback was last logged


def supervisor_pass(sup: Any, hb: Any) -> dict:
    """One pass of the supervisor loop. An unexpected exception in evaluate() is logged and published as a halt
    (`supervisor_error`, Supervisor.error_halt) and the loop carries on, so a corrupt state file or a bug never kills
    run_supervisor (a failing write of that halt leaves the heartbeat to go stale, which engines also halt on)."""
    try:
        st: dict = sup.evaluate()
    except Exception as exc:
        kind, mono = type(exc).__name__, time.monotonic()
        logged, every = patched("_SUP_ERR_LOGGED", _SUP_ERR_LOGGED), patched("SUP_ERR_LOG_S", SUP_ERR_LOG_S)
        last = logged.get(kind)
        if last is None or mono - last >= every:     # review LOW-2: one traceback per type per SUP_ERR_LOG_S
            logged[kind] = mono
            log.exception("supervisor evaluate failed (%s); publishing a halt (supervisor_error)", kind)
        try:
            st = sup.error_halt(exc)
        except Exception:
            log.exception("supervisor halt could not be written; engines halt on the stale heartbeat")
            st = {"halt": True, "reasons": ["supervisor_error"]}
    hb.beat()
    if st["halt"]:
        log.warning("HALT %s", st["reasons"])
    return st


def run_supervisor() -> None:
    from goldbot.config import load_settings
    from goldbot.ops.health import Heartbeat
    from goldbot.risk.supervisor import Supervisor, SupervisorLimits
    sup = Supervisor("state", SupervisorLimits.from_settings(load_settings().risk, ceilings=resolve(demo_ceilings)()))
    hb = Heartbeat("state", "supervisor")          # S5: every service writes state/heartbeat_<service>.json
    while True:
        resolve(supervisor_pass)(sup, hb)
        time.sleep(5)


def demo_ceilings() -> Any:
    """ADR 0032: the combined caps' ceilings when every enabled account in config/accounts.yaml is demo with the owner's
    ceilings, else None (the defaults). An unreadable registry gives None: the strict side."""
    from goldbot.ops.accounts import load_accounts
    from goldbot.risk.limits_profile import supervisor_ceilings
    try:
        return supervisor_ceilings(list(load_accounts().values()))
    except Exception as exc:
        log.error("account registry unreadable (%s): the supervisor keeps its default caps", type(exc).__name__)
        return None
