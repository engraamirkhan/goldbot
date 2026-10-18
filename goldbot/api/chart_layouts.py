"""The owner's saved TradingView chart layouts (Chart tab): up to 3, each a name and a https://www.tradingview.com/chart/
URL. The URL identifies the owner's TradingView layout, so it is stored only in aux.db (table chart_layouts), shown only to
the owner and never written to the audit trail."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from goldbot.db.connection import Database

# https://www.tradingview.com/chart/<id>/ or /chart/<name>/<id>/ ; optional query/fragment-free: the id is the identifier
_PATH = re.compile(r"^/chart/(?:[A-Za-z0-9_-]{1,60}/)?[A-Za-z0-9]{4,20}/?$")


def valid_layout_url(url: str) -> bool:
    try:
        u = urlsplit(url.strip())
        port = u.port
    except ValueError:
        return False
    return (u.scheme == "https" and u.hostname == "www.tradingview.com" and port is None
            and not u.username and not u.password and not u.query and not u.fragment
            and bool(_PATH.match(u.path)))


def read_layouts(db: Database) -> list[tuple[str, str]]:
    with db.connection() as conn:
        return [(str(r[0]), str(r[1])) for r in conn.execute("SELECT name, url FROM chart_layouts ORDER BY slot")]


def write_layouts(db: Database, layouts: list[tuple[str, str]]) -> None:
    with db.write() as conn:
        conn.execute("DELETE FROM chart_layouts")
        conn.executemany("INSERT INTO chart_layouts (slot, name, url) VALUES (?, ?, ?)",
                         [(i, n, u) for i, (n, u) in enumerate(layouts, start=1)])
