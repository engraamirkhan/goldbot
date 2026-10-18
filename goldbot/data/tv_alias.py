"""Our names for TradingView studies (owner rule: our own names, never a vendor's indicator name).

TradingView's metadata names a study with its author's product name. Nothing the owner reads (`tv-pull status/sync`,
`GET /api/tradingview` and its dashboard card, notices, logs) shows that name: `alias_map` turns it into ours.

* The settings map `tv_pull.aliases` is keyed by `key_of(text)`, the first 8 hex of the sha256 of a study's name or of
  its plot-id fingerprint (`plots_key`: the sorted plot ids, the structural kind); no vendor name is written in the
  repo. The owner's keys live in settings.local.yaml on the brain (`python -m goldbot.data.tv_alias TEXT` prints one).
* A study with no entry is "Indicator N", N counting the unmatched ones of the same listing in sorted order.
* Stored data keeps the raw source name (the feature id `tv_{study}_{meta_hash}_{tf}_{plot_id}` and the rows are
  unchanged); only what is displayed is aliased.
"""
from __future__ import annotations

import hashlib
import re
import sys
from collections.abc import Mapping, Sequence


def key_of(text: str) -> str:
    """8 hex of the sha256 of `text` made path-safe the way the store's source name is (webhook.inbox.source_of), so a
    study's name and the partition it is stored under give the same key."""
    return hashlib.sha256(re.sub(r"[^A-Za-z0-9._@+-]+", "_", text).encode()).hexdigest()[:8]


def plots_key(plot_ids: Sequence[str]) -> str:
    """Structural fingerprint of a study: its plot ids, sorted (a plot id is not the author's product name)."""
    return key_of("plots:" + ",".join(sorted(plot_ids)))


def name_keys(name: str, plot_ids: Sequence[str] = ()) -> list[str]:
    """The keys to try for a study, most specific last: its name, then its plot fingerprint (when known)."""
    return [key_of(name)] + ([plots_key(plot_ids)] if plot_ids else [])


def alias_map(aliases: Mapping[str, str], entries: Mapping[str, Sequence[str]]) -> dict[str, str]:
    """`entries`: raw name -> the keys to try for it. Returns raw name -> display name: the first key found in
    `aliases` wins, else "Indicator N"."""
    out: dict[str, str] = {}
    n = 0
    for raw in sorted(entries):
        hit = next((aliases[k] for k in entries[raw] if k in aliases), None)
        if hit is None:
            n += 1
        out[raw] = hit or f"Indicator {n}"
    return out


if __name__ == "__main__":
    print(key_of(" ".join(sys.argv[1:])))
