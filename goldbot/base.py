"""Shared pydantic bases for goldbot's data types.

Every record that crosses a module boundary (orders, risk inputs, proposals, settings, agent identity) is a
pydantic model so values are validated on construction and assignment, and serialise with model_dump().
`arbitrary_types_allowed` lets records carry pandas/numpy values (timestamps, frames, index arrays) checked
by isinstance; `extra="forbid"` turns a misspelt field into an error instead of a silent default.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Record(BaseModel):
    """Mutable record, validated on construction and on attribute assignment."""

    model_config = ConfigDict(arbitrary_types_allowed=True, validate_assignment=True, extra="forbid")


class FrozenRecord(BaseModel):
    """Immutable record (hashable when its fields are); change it with model_copy(update=...)."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="forbid")
