"""The dashboard recognises a few server refusals by their exact words (web/src/lib/api.ts `SERVER_SAYS`): the sign-in
lockout (Login State F), the step-up refusal and the step-up lockout (Users). If the server's wording changed without
the client, the screen would show raw text or count a lockout as a wrong code, so the two are pinned together here."""
from __future__ import annotations

import re
from pathlib import Path

from goldbot.api import app as app_module
from goldbot.api import auth as auth_module

API_TS = Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "api.ts"


def _server_says() -> dict[str, str]:
    block = re.search(r"export const SERVER_SAYS = \{(.*?)\} as const;", API_TS.read_text(), re.S)
    assert block, "web/src/lib/api.ts must define SERVER_SAYS"
    return dict(re.findall(r'(\w+): "([^"]*)"', block.group(1)))


def test_the_web_client_knows_the_servers_exact_refusal_words() -> None:
    assert _server_says() == {
        "signInLocked": auth_module.SIGN_IN_LOCKED,
        "stepupRequired": app_module.STEPUP_REQUIRED,
        "stepupLocked": app_module.STEPUP_LOCKED,
    }
