from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS = ROOT / "config" / "settings.yaml"

TF_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1w": 7 * 86400,
}


@lru_cache(maxsize=4)
def load_settings(path: str | Path = DEFAULT_SETTINGS) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def tf_seconds(tf: str) -> int:
    try:
        return TF_SECONDS[tf]
    except KeyError as exc:
        raise ValueError(f"unknown timeframe {tf!r}; known: {sorted(TF_SECONDS)}") from exc
