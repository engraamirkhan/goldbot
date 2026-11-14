"""1d/1w long-history health check (BACKLOG 251, BACKLOG 144 blocker 4).

A new area module, registered from goldbot/ops/health.py (which imports it with the other checks): the
`history_gap:<account>` check of checks/engine.py judges the intraday window's holes on refused closes; a 1d/1w arm
held back by its long daily history is journalled once per reason, so it never reaches that check's count."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from goldbot.ops.checks._base import AccountRef, Check, _read_json
from goldbot.ops.checks.registry import BLOCK_ACCOUNT, register_check

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext

LONG_HISTORY_WARN_AFTER = pd.Timedelta(days=1)    # BACKLOG 251: "a history_gap:<tf> that has lasted more than a day"


@register_check("account", block=BLOCK_ACCOUNT, pos=47, pass_account=True)
def check_long_history(ctx: HealthContext, account: AccountRef) -> Check | None:
    """long_history:<account>. Design rule (engine/long_bars.py): "a missing file or a history that does not reach the
    engine's window keeps the arm quiet (`history_gap:<tf>`), never a crash"; quiet must not mean unseen. WARN, and the
    owner is told (NOTIFY_ON_WARN), when a 1d/1w arm the engine publishes in `long_history_gap` has been held back for
    more than LONG_HISTORY_WARN_AFTER (the daily refresh, `long_history_refresh`, has not closed it); OK while every
    held-back arm is younger. None without an engine report or a held-back arm (1d/1w dark: always None)."""
    try:
        e = _read_json(ctx.state_dir / f"engine_{account.account_id}.json")
    except (ValueError, OSError):
        return None                                  # engine:<account> reports a missing or unreadable file
    raw = e.get("long_history_gap") if isinstance(e, dict) else None
    held = {a: g for a, g in raw.items() if isinstance(g, dict)} if isinstance(raw, dict) else {}
    if not held:
        return None
    name = f"long_history:{account.account_id}"
    old: list[str] = []
    for arm, g in sorted(held.items()):
        try:
            since = pd.Timestamp(str(g.get("since")))
            since = since.tz_localize("UTC") if since.tzinfo is None else since
        except ValueError:
            old.append(f"{arm} ({g.get('tf')}: {g.get('reason')}, since unreadable)")
            continue
        if ctx.now - since > LONG_HISTORY_WARN_AFTER:
            old.append(f"{arm} ({g.get('tf')}: {g.get('reason')} since {since:%Y-%m-%d %H:%M} UTC)")
    if not old:
        return Check(name=name, status="ok",
                     reason=f"{len(held)} 1d/1w arm(s) held back for less than a day: the daily refresh may close it")
    return Check(name=name, status="warn",
                 reason=(f"{len(old)} 1d/1w arm(s) held back history_gap for more than a day: {'; '.join(old)}; the "
                         "long daily history does not reach the engine window (job long_history_refresh, RUNBOOK "
                         "'Long daily history')")[:600])
