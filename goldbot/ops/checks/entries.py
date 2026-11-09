"""Entry supply and silence of the demo exploring accounts: entry_silence:<account> (goldbot/ops/entry_silence.py)."""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from goldbot.ops.checks._base import Check
from goldbot.ops.checks.registry import BLOCK_ACCOUNT, register_check

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext


@register_check("account", block=BLOCK_ACCOUNT, pos=65, when=lambda ctx: ctx.entry_supply is not None)
def check_entry_silence(ctx: HealthContext, account_id: str) -> Check | None:
    """Incident 2026-11-09 (vantage-demo: no entry for 9.5 h of open market, nothing alerted). WARN, told on Telegram
    (NOTIFY_ON_WARN), when the exploring demo account (a) has no arm that may enter while its market has been open
    today and its day budget is unspent, or (b) went SILENCE_MIN minutes of London/New York market time without a
    fill today while arms may enter. Both are day-stable, so at most one warning per account per UTC day. None for
    an account that does not explore. Reporting only.

    Code review 2026-11-09: an arm config that cannot be read is read once per run (the exception is memoized) and
    every account is OK "not judged", never a FAIL per account (the settings and accounts checks report the config);
    a torn aux.db read keeps this process's last verdict for the account (no WARN -> OK -> WARN that re-tells the
    owner), or reports nothing when there is none (exploration_learning reports an unreadable aux.db)."""
    from goldbot.ops import entry_silence as es
    assert ctx.entry_supply is not None
    if "entry_supply" not in ctx.memo:                 # the config is read once per health run, not once per account
        try:
            ctx.memo["entry_supply"] = ctx.entry_supply()
        except Exception as exc:                       # noqa: BLE001 - reporting only: the config checks own the fault
            ctx.memo["entry_supply"] = exc
    view = ctx.memo["entry_supply"]
    name = f"entry_silence:{account_id}"
    if isinstance(view, Exception):
        return Check(name=name, status="ok", reason=f"not judged: arm config unreadable ({type(view).__name__})")
    s = view.get(account_id)
    if s is None:
        return None
    key = (str(ctx.state_dir), account_id)
    try:
        v = es.evaluate(s, ctx.state_dir / "aux.db", account_id, ctx.now, ctx.sessions)
    except sqlite3.Error as exc:
        last = es._LAST.get(key)
        if last is None:
            return None
        return Check(name=name, status=last.status,
                     reason=f"{last.reason} [aux.db unreadable now ({type(exc).__name__}): last verdict kept]"[:700])
    out = Check(name=name, status="warn" if v.no_supply or v.silent else "ok", reason=es.describe(v))
    es._LAST[key] = out
    return out
