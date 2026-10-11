"""ADR 0010: persisted state is read tolerantly, built strictly (goldbot/base.py load_record / load_record_json)."""
from __future__ import annotations

import json
import logging

import pytest
from pydantic import Field, ValidationError

from goldbot.base import FrozenRecord, Record, load_record, load_record_json, schema_skew


class _Leaf(FrozenRecord):
    z: int = 0


class _Mid(Record):
    y: int
    items: list[_Leaf] = Field(default_factory=list)
    by_key: dict[str, _Leaf] = Field(default_factory=dict)


class _Top(Record):
    x: int
    mid: _Mid | None = None


NEWER = {"x": 1, "top_new": "a",
         "mid": {"y": 2, "mid_new": 3, "items": [{"z": 4, "leaf_new": 5}], "by_key": {"k": {"z": 6, "map_new": 7}}}}


def test_fields_from_a_newer_release_are_ignored_at_every_nesting_level():
    for t in (load_record(_Top, NEWER, source="test.levels"),
              load_record_json(_Top, json.dumps(NEWER), source="test.levels")):
        assert t.x == 1 and t.mid is not None and t.mid.y == 2
        assert t.mid.items == [_Leaf(z=4)] and t.mid.by_key == {"k": _Leaf(z=6)}
        assert t.model_dump() == {"x": 1, "mid": {"y": 2, "items": [{"z": 4}], "by_key": {"k": {"z": 6}}}}
        assert t.model_extra is None and t.mid.model_extra is None      # nothing kept to be written back


def test_a_record_without_unknown_fields_loads_unchanged():
    same = {"x": 1, "mid": {"y": 2, "items": [{"z": 4}]}}
    assert load_record(_Top, same, source="test.same") == _Top.model_validate(same)


def test_construction_with_an_unknown_field_still_raises():
    with pytest.raises(ValidationError):
        _Top(x=1, top_new="a")                                          # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        _Top.model_validate(NEWER)                                      # plain validation stays forbid
    with pytest.raises(ValidationError):
        _Leaf(z=1, leaf_new=2)                                          # type: ignore[call-arg]


def test_a_wrong_type_a_missing_field_or_bad_json_still_fails_on_load():
    with pytest.raises(ValidationError):
        load_record(_Top, {"x": "not a number", "new": 1}, source="test.bad")
    with pytest.raises(ValidationError):
        load_record(_Top, {"mid": {"y": 1}, "new": 1}, source="test.bad")         # x missing
    with pytest.raises(ValidationError):
        load_record(_Top, {"x": 1, "mid": {"items": [], "new": 1}}, source="test.bad")   # nested y missing
    with pytest.raises(ValueError):
        load_record_json(_Top, '{"x": 1,', source="test.bad")


def test_an_unknown_field_is_logged_once_per_source_model_and_fields_and_counted(caplog):
    key = ("test.once", "_Top", ("mid.by_key.{}.map_new", "mid.items.[].leaf_new", "mid.mid_new", "top_new"))
    caplog.set_level(logging.WARNING, logger="goldbot.base")
    for _ in range(3):
        load_record(_Top, NEWER, source="test.once")
    warnings = [r for r in caplog.records if "test.once" in r.getMessage()]
    assert len(warnings) == 1 and "top_new" in warnings[0].getMessage()
    assert schema_skew()[key] == 3
    load_record(_Top, {"x": 1, "other_new": 1}, source="test.once")      # different fields: logged again
    assert len([r for r in caplog.records if "test.once" in r.getMessage()]) == 2
    assert schema_skew()[("test.once", "_Top", ("other_new",))] == 1
    load_record(_Top, {"x": 1}, source="test.clean")
    assert not any(k[0] == "test.clean" for k in schema_skew())          # a clean read is not counted
