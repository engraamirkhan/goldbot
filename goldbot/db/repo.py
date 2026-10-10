"""Typed table access for pydantic Records (docs/proposals/2026-10-state-store.md, section 3, "Record mapping").

"Keys and filter fields become real columns. The whole pydantic record goes in
`body TEXT NOT NULL CHECK (json_valid(body))`, written with model_dump_json() and read with model_validate_json(),
so extra="forbid" still rejects drift."

    users = RecordRepo("users", User, key="email",
                       columns={"email": lambda u: u.email, "role": lambda u: u.role, "enabled": lambda u: int(u.enabled)})
    with db.write() as conn:
        users.upsert(conn, user)

The repository never opens transactions itself: the caller decides what commits together.
"""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

R = TypeVar("R", bound=BaseModel)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _ident(name: str) -> str:
    if not _IDENT.fullmatch(name):
        raise ValueError(f"bad SQL identifier {name!r}")
    return name


class RecordRepo(Generic[R]):
    def __init__(self, table: str, model: type[R], *, key: str, columns: Mapping[str, Callable[[R], Any]]):
        if key not in columns:
            raise ValueError("the key must be one of the columns")
        self.table = _ident(table)
        self.model = model
        self.key = _ident(key)
        self.columns = {_ident(c): f for c, f in columns.items()}

    def row(self, rec: R) -> dict[str, Any]:
        return {**{c: f(rec) for c, f in self.columns.items()}, "body": rec.model_dump_json()}

    def upsert(self, conn: sqlite3.Connection, rec: R) -> None:
        row = self.row(rec)
        cols = ", ".join(row)
        marks = ", ".join(f":{c}" for c in row)
        updates = ", ".join(f"{c}=excluded.{c}" for c in row if c != self.key)
        conn.execute(f"INSERT INTO {self.table} ({cols}) VALUES ({marks}) "
                     f"ON CONFLICT({self.key}) DO UPDATE SET {updates}", row)

    def insert_if_absent(self, conn: sqlite3.Connection, rec: R) -> bool:
        """True if inserted, False if a row with this key already existed (first writer wins)."""
        row = self.row(rec)
        cur = conn.execute(f"INSERT INTO {self.table} ({', '.join(row)}) VALUES ({', '.join(':' + c for c in row)}) "
                           f"ON CONFLICT({self.key}) DO NOTHING", row)
        return cur.rowcount == 1

    def get(self, conn: sqlite3.Connection, key: Any) -> R | None:
        r = conn.execute(f"SELECT body FROM {self.table} WHERE {self.key} = ?", (key,)).fetchone()
        return self.model.model_validate_json(r[0]) if r else None

    def select(self, conn: sqlite3.Connection, where: str = "", params: Sequence[Any] | Mapping[str, Any] = (),
               order_by: str | None = None) -> list[R]:
        """`where` is a trusted SQL fragment over this table's columns (never user input); values go in `params`."""
        sql = f"SELECT body FROM {self.table}"
        if where:
            sql += f" WHERE {where}"
        sql += f" ORDER BY {_ident(order_by or self.key)}"
        return [self.model.model_validate_json(b) for (b,) in conn.execute(sql, params)]

    def delete(self, conn: sqlite3.Connection, key: Any) -> bool:
        return conn.execute(f"DELETE FROM {self.table} WHERE {self.key} = ?", (key,)).rowcount == 1

    def count(self, conn: sqlite3.Connection) -> int:
        return int(conn.execute(f"SELECT COUNT(*) FROM {self.table}").fetchone()[0])


__all__ = ["RecordRepo"]
