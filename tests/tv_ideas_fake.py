"""A synthetic TradingView ideas API for tests/test_tv_ideas.py. Every author, title and text here is invented: the real
ideas are third-party content and never enter the repo (no recorded responses, no real idea text).

It reproduces the behaviour measured on 2026-10-28 that the collector depends on:
* `/api/v1/ideas/?by=<author>&sort=recent_extended` counts the whole catalogue, newest activity (an update or the
  publication) first; any other sort counts at most `CAP` (1000);
* a query is served only up to `CAP` items: a page beyond it answers 400 `bad_pagination_request`;
* `exchange`, `symbol`, `type`, `is_video` and `q` (a word of the title or text) narrow a query; unknown parameters are
  ignored; `per_page` up to 100;
* the listing's description is cut at 500 characters; `/api/v1/ideas/<uuid>/` has the full text and the updates.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlsplit

CAP = 1000


class FakeHttpError(RuntimeError):
    pass


def make_idea(i: int, *, author: str = "SynthAuthor", exchange: str = "FX", ticker: str = "XAUUSD",
              created: float = 1_700_000_000.0, direction: int = 1, interval: str = "240", title: str | None = None,
              description: str | None = None, updates: list[dict[str, Any]] | None = None, likes: int = 0,
              is_education: bool = False, is_video: bool = False) -> dict[str, Any]:
    """One idea in the detail endpoint's shape (the listing item is derived from it)."""
    uuid = f"u{i:07d}"
    ups = updates or []
    return {
        "id": 10_000_000 + i, "uuid": uuid, "name": title or f"Synthetic idea {i}",
        "description": description if description is not None else f"Synthetic text {i}.",
        "created_timestamp": created, "last_updated_timestamp": max((u["ts"] for u in ups), default=None),
        "chart_url": f"/chart/{ticker}/{uuid}-synthetic-idea-{i}/", "direction": direction, "interval": interval,
        # the detail's symbol has no `name`/`full_name` (measured: resolved_symbol, pro_symbol, exchange, short_name)
        "symbol": {"resolved_symbol": f"{exchange}:{ticker}", "pro_symbol": f"{exchange}:{ticker}",
                   "short_name": ticker, "exchange": exchange},
        "user": {"id": 1, "username": author},
        "image": {"big": f"https://s3.example.invalid/h/{uuid}_big.png"},
        "is_education": is_education, "video": {"id": 1} if is_video else None,
        "updates": [{"id": u["id"], "type": u.get("type", "trading"), "value": u.get("value", "active"),
                     "label": u.get("label", "Trade active"), "description": u.get("description", ""),
                     "created_at": _iso(u["ts"])} for u in ups],
        "_likes": likes,
    }


def _iso(ts: float) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def listing_item(d: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": d["id"], "image_url": d["uuid"], "name": d["name"], "description": d["description"][:500],
        "date_timestamp": int(d["created_timestamp"]),
        "updated_date_timestamp": int(d["last_updated_timestamp"]) if d["last_updated_timestamp"] else None,
        "chart_url": "https://www.tradingview.com" + d["chart_url"], "is_video": d["video"] is not None,
        "is_education": d["is_education"], "user": dict(d["user"]),
        "symbol": {"name": d["symbol"]["pro_symbol"], "full_name": d["symbol"]["pro_symbol"],
                   "short_name": d["symbol"]["short_name"], "exchange": d["symbol"]["exchange"],
                   "interval": d["interval"], "direction": d["direction"]},
        "image": dict(d["image"]), "likes_count": d["_likes"],
    }


class FakeIdeasApi:
    """The `TvNetwork` the collector talks to (GET only; a websocket is never opened)."""

    def __init__(self, ideas: list[dict[str, Any]]):
        self.ideas = list(ideas)
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.fail_next = 0

    # ---- TvNetwork
    def get(self, url: str, headers: Mapping[str, str]) -> str:
        self.calls.append((url, dict(headers)))
        if self.fail_next:
            self.fail_next -= 1
            raise FakeHttpError("503 Service Unavailable")
        u = urlsplit(url)
        assert u.hostname == "www.tradingview.com"
        parts = [p for p in u.path.split("/") if p]
        if parts == ["api", "v1", "ideas"]:
            return json.dumps(self._listing({k: v[-1] for k, v in parse_qs(u.query).items()}))
        if len(parts) == 4 and parts[:3] == ["api", "v1", "ideas"]:
            for d in self.ideas:
                if d["uuid"] == parts[3]:
                    return json.dumps({k: v for k, v in d.items() if not k.startswith("_")})
            raise FakeHttpError("404 Not Found")
        raise FakeHttpError("404 Not Found")

    def open_socket(self, url: str, headers: Mapping[str, str]) -> Any:  # pragma: no cover - never called
        raise AssertionError("the ideas collector never opens a websocket")

    # ---- the listing
    def _listing(self, q: dict[str, str]) -> dict[str, Any]:
        rows = [d for d in self.ideas if d["user"]["username"].lower() == q.get("by", d["user"]["username"]).lower()]
        if "exchange" in q:
            rows = [d for d in rows if d["symbol"]["exchange"] == q["exchange"]]
        if "symbol" in q:
            rows = [d for d in rows if d["symbol"]["short_name"] == q["symbol"].split(":")[-1]]
        if q.get("type") == "education":
            rows = [d for d in rows if d["is_education"]]
        elif q.get("type") == "trade":
            rows = [d for d in rows if not d["is_education"]]
        if "is_video" in q:
            want = q["is_video"] == "true"
            rows = [d for d in rows if (d["video"] is not None) == want]
        if "q" in q:
            word = q["q"].lower()
            rows = [d for d in rows if word in (d["name"] + " " + d["description"]).lower().split()]
        sort = q.get("sort", "")
        if sort == "recent_extended":
            rows.sort(key=lambda d: max(d["created_timestamp"], d["last_updated_timestamp"] or 0), reverse=True)
            count = len(rows)
        elif sort == "trending":
            rows.sort(key=lambda d: (d["_likes"], d["created_timestamp"]), reverse=True)
            count = min(len(rows), CAP)
        else:
            rows.sort(key=lambda d: d["created_timestamp"], reverse=True)
            count = min(len(rows), CAP)
        per = min(int(q.get("per_page", 20)), 100)
        page = int(q.get("page", 1))
        pages = max(1, math.ceil(count / per))
        if (page - 1) * per >= CAP or page < 1:
            raise FakeHttpError("400 Bad Request: bad_pagination_request")
        chunk = rows[:CAP][(page - 1) * per: page * per]
        return {"count": count, "page_size": per, "page_count": pages,
                "next": None if page >= pages or page * per >= CAP else f"?page={page + 1}",
                "previous": None, "results": [listing_item(d) for d in chunk]}
