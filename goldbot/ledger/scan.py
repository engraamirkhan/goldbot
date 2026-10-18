"""Secret and identifier scanner for the program ledger.

The repo is public and the ledger exports to docs/ledger: "never commit or push secrets or account identifiers
(passwords, tokens, MT5 login numbers, Telegram ids, hosts)" (CLAUDE.md). `scan` returns rule names only, never the
matched text, so a refusal message cannot leak the value. It is fail closed: `scan` lets any internal error propagate
as `ScanError`, and the store treats that as a refusal."""
from __future__ import annotations

import re

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple((n, re.compile(p, re.IGNORECASE)) for n, p in (
    ("github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    ("cloud-or-api-key", r"\b(?:AKIA[0-9A-Z]{12,}|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})"),
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY"),
    ("telegram-bot-token", r"\b\d{8,10}:[A-Za-z0-9_-]{30,}"),
    ("credential-assignment", r"\b(?:password|passwd|pwd|secret|token|api[_ -]?key|access[_ -]?key)\b\s*[:=]\s*\S{6,}"),
    ("mt5-login", r"\b(?:mt5|metatrader|account|login)\b\D{0,12}\d{6,}"),
    ("telegram-id", r"\b(?:telegram|chat[_ ]?id|tg)\b\D{0,12}-?\d{6,}"),
    ("ip-address", r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    ("email", r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    ("host", r"\b[\w-]+(?:\.[\w-]+)*\.(?:oraclecloud\.com|internal|local|lan)\b"),
))


class ScanError(RuntimeError):
    """The scanner itself failed: the caller must refuse (fail closed)."""


def _findings(text: str) -> list[str]:
    return [name for name, rx in _RULES if rx.search(text)]


def scan(text: str) -> list[str]:
    """Rule names that match `text` (empty: clean). Raises ScanError when the scan itself fails."""
    try:
        return _findings(text)
    except Exception as exc:                     # noqa: BLE001 - any failure of the scanner is a refusal
        raise ScanError(f"scanner failed ({type(exc).__name__})") from None
