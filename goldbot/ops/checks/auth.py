"""Auth and mail health checks: keyring secrets (presence only), the email sign-in link."""
from __future__ import annotations

from typing import TYPE_CHECKING

from goldbot.ops.checks._base import OPTIONAL_SECRETS, REQUIRED_SECRETS, Check, _epoch
from goldbot.ops.checks.registry import BLOCK_STATIC, BLOCK_TAIL, register_check

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext


@register_check("static", block=BLOCK_STATIC, pos=30)
def check_secrets(ctx: HealthContext) -> Check:
    """Presence only. Values are never returned, compared or logged."""
    def present(key: str) -> bool:
        try:
            return bool(ctx.get_secret(key))
        except Exception:
            return False
    required = list(REQUIRED_SECRETS)
    missing = [k for k in required if not present(k)]
    for a in ctx.accounts:     # a terminal on this machine needs its password; a bridged one needs the bridge token
        pw, bridge = f"mt5-{a.account_id}", f"mt5-bridge-token-{a.account_id}"
        url = f"mt5-bridge-url-{a.account_id}"
        required.append(pw)
        bridged = present(bridge) and present(url)      # exactly when the engine uses the bridge (bridge_endpoint)
        if not (present(pw) or bridged):
            missing.append(f"{pw} (or {url} + {bridge} when the terminal runs behind the bridge)")
    optional = [k for k in OPTIONAL_SECRETS if not present(k)]
    hint = " (python -m goldbot.ops.accounts set <key>)"
    if missing:
        extra = f"; optional missing: {', '.join(optional)}" if optional else ""
        return Check(name="secrets", status="fail", reason=f"missing in keyring: {', '.join(missing)}{extra}{hint}")
    if optional:
        return Check(name="secrets", status="warn", reason=f"optional missing: {', '.join(optional)}{hint}")
    return Check(name="secrets", status="ok", reason=f"{len(required) + len(OPTIONAL_SECRETS)} keys present")


@register_check("global", block=BLOCK_TAIL, pos=120)
def check_email_link(ctx: HealthContext) -> Check:
    """The owner's email sign-in link hitting its hourly limit again and again (goldbot/api/auth.py
    `email_link_health_note`): warn, never fail, since password sign-in still works. Never names the address."""
    from goldbot.api.auth import email_link_health_note
    owner = ctx.settings.auth.owner_email if ctx.settings is not None else None
    note = email_link_health_note(ctx.state_dir, owner, _epoch(ctx.now))
    if note:
        return Check(name="email_link", status="warn", reason=note)
    return Check(name="email_link", status="ok", reason="no repeated sign-in link rate limiting")
