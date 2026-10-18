"""GET-only HTTP with a >= 1 s gap between requests. No method here can write to the account: there is no
post/put/delete on purpose (read-only contract)."""
from __future__ import annotations

import time
from typing import Any

import requests

from .client import ORIGIN, UA

MIN_GAP_S = 1.0


class Http:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Origin": ORIGIN, "Referer": ORIGIN + "/"})
        self._last = 0.0

    def _get(self, url: str, cookies: dict[str, str] | None, params: dict[str, Any] | None,
             headers: dict[str, str] | None = None) -> requests.Response:
        wait = MIN_GAP_S - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            return self.s.get(url, cookies=cookies, params=params, headers=headers, timeout=30, allow_redirects=False)
        finally:
            self._last = time.monotonic()

    def get_text(self, url: str, *, cookies: dict[str, str] | None = None, params: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None) -> str:
        r = self._get(url, cookies, params, headers)
        r.raise_for_status()
        return r.text

    def get_json(self, url: str, *, cookies: dict[str, str] | None = None, params: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None) -> tuple[int, Any]:
        """(status, parsed JSON or None). Never raises on HTTP status, so discovery can try alternatives."""
        r = self._get(url, cookies, params, headers)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, None
