"""Column accumulator for feature functions.

`out = pd.DataFrame(index=...); out["a"] = ...` inserts one column at a time, and each insert costs about half a
millisecond in pandas 3 (block manager + Arrow-backed string column index); a feature set of ~100 columns paid
that on every frame. `Columns` collects the same values in a dict and builds the frame once: same columns, same
order, same dtypes, same values. Reading a column back gives a Series on the frame's index, as the DataFrame did.
"""
from __future__ import annotations

from typing import Any

import pandas as pd


class Columns(dict[str, Any]):
    def __init__(self, index: pd.Index, data: dict[str, Any] | None = None):
        super().__init__(data or {})
        self.index = index

    def __getitem__(self, key: str) -> pd.Series:
        v = super().__getitem__(key)
        return v if isinstance(v, pd.Series) else pd.Series(v, index=self.index)

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(dict(self), index=self.index)
