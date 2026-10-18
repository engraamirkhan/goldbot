"""The owner's TradingView session, read from the OS keyring only (goldbot.ops.accounts), never printed or written.

Keyring entries (service "goldbot"), stored by the owner with the hidden prompt. `--with keyring` matters: without
the package, `accounts set` falls back to a plaintext file instead of the macOS Keychain.
  uv run --with keyring python -m goldbot.ops.accounts set tradingview-sessionid       # browser cookie `sessionid`
  uv run --with keyring python -m goldbot.ops.accounts set tradingview-sessionid-sign  # cookie `sessionid_sign`

`login()` GETs the signed-in home page once and extracts the websocket auth token, the user id and the plan. The
token and id are kept in memory only; callers get a `Session` whose repr hides them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import requests

from goldbot.ops.accounts import SERVICE

from .http import Http


def get_secret(key: str) -> str | None:
    """OS keyring ONLY. goldbot.ops.accounts.get_secret would also read the plaintext state/.secrets.json fallback,
    which is where `accounts set` silently writes when the `keyring` package is not installed (it is in the `live`
    extra only); a browser session cookie must not live in a plaintext file, so that fallback is refused here."""
    import keyring

    try:
        return keyring.get_password(SERVICE, key)
    except Exception:            # noqa: BLE001 - no backend = not present
        return None

KEY_SESSIONID = "tradingview-sessionid"
KEY_SESSIONID_SIGN = "tradingview-sessionid-sign"
HOME = "https://www.tradingview.com/"


class AuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class Session:
    cookies: dict[str, str] = field(repr=False)
    auth_token: str = field(repr=False)
    user_id: int = field(repr=False)
    plan: str            # "" = free; "pro", "pro_plus", "pro_premium", ...

    def secrets(self) -> list[str]:
        """Everything a recorder must redact."""
        return [*self.cookies.values(), self.auth_token, str(self.user_id)]


def keys_present() -> dict[str, bool]:
    return {k: bool(get_secret(k)) for k in (KEY_SESSIONID, KEY_SESSIONID_SIGN)}


def _cookies() -> dict[str, str]:
    sid = get_secret(KEY_SESSIONID)
    if not sid:
        raise AuthError(f"keyring entry {KEY_SESSIONID} is missing: run "
                        f"`uv run --with keyring python -m goldbot.ops.accounts set {KEY_SESSIONID}`")
    cookies = {"sessionid": sid.strip()}
    sign = get_secret(KEY_SESSIONID_SIGN)
    if sign:
        cookies["sessionid_sign"] = sign.strip()
    return cookies


def login(http: Http) -> Session:
    cookies = _cookies()
    try:
        html = http.get_text(HOME, cookies=cookies)
    except requests.RequestException as exc:      # message may carry the URL only, never cookies
        raise AuthError(f"home page fetch failed ({type(exc).__name__})") from None
    tok = re.search(r'"auth_token"\s*:\s*"([^"]+)"', html)
    if not tok:
        raise AuthError("not signed in: no auth_token on the home page (cookie expired or wrong; re-copy both cookies)")
    uid = re.search(r'"user"\s*:\s*\{[^{}]*?"id"\s*:\s*(\d+)', html) or re.search(r'"id"\s*:\s*(\d+)\s*,\s*"username"', html)
    plan = re.search(r'"pro_plan"\s*:\s*"([^"]*)"', html)
    return Session(cookies=cookies, auth_token=tok.group(1), user_id=int(uid.group(1)) if uid else 0,
                   plan=plan.group(1) if plan else "")
