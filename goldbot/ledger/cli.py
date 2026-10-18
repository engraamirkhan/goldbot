"""`python -m goldbot.ops.run ledger add|query|resume|export`: the program ledger from the command line.

  ledger add TABLE --key K (--body TEXT | --body-file PATH|-) --source S [--private] [--field name=value ...]
             [--blob-file PATH]            append a row (a new version when the key exists); exit 2 when refused
  ledger query TABLE [--text T] [--private] [--history] [--json]
                                           latest public rows (--private includes private rows; --history all versions)
  ledger resume                            print the latest resume snapshot (exit 1 when there is none)
  ledger export [--out docs/ledger]        write the public rows as generated markdown

Exit codes: 0 ok, 1 nothing found, 2 refused or bad arguments."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from goldbot.ledger.models import TABLE_FIELDS, NewEntry
from goldbot.ledger.store import Ledger, LedgerRefused

MAX_BLOB_FILE_BYTES = 8 * 1024 * 1024        # a larger file is refused before it is read (only <= 256 KB is stored)
EXPORT_DIR = Path("docs/ledger")


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="run.py ledger", description="The program ledger (state/program.db).")
    ap.add_argument("--state", default="state", help="state directory (program.db)")
    sub = ap.add_subparsers(dest="action", required=True)
    add = sub.add_parser("add", help="append a row")
    add.add_argument("table", choices=sorted(TABLE_FIELDS))
    add.add_argument("--key", required=True)
    body = add.add_mutually_exclusive_group(required=True)
    body.add_argument("--body")
    body.add_argument("--body-file", help="read the body from this file ('-': stdin)")
    add.add_argument("--source", required=True)
    add.add_argument("--private", action="store_true")
    add.add_argument("--field", action="append", default=[], metavar="NAME=VALUE", help="the table's own columns")
    add.add_argument("--blob-file", help="artifacts: the file whose content is recorded")
    q = sub.add_parser("query", help="show latest rows")
    q.add_argument("table", choices=sorted(TABLE_FIELDS))
    q.add_argument("--text")
    q.add_argument("--private", action="store_true", help="include private rows")
    q.add_argument("--history", action="store_true", help="every version, not only the latest")
    q.add_argument("--json", action="store_true")
    sub.add_parser("resume", help="print the latest resume snapshot")
    ex = sub.add_parser("export", help="write public rows to docs/ledger")
    ex.add_argument("--out", default="docs/ledger")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    try:
        ledger = Ledger.open(a.state)
        if a.action == "add":
            return _add(ledger, a)
        if a.action == "query":
            rows = ledger.query(a.table, text=a.text, include_private=a.private, history=a.history)
            if a.json:
                print(json.dumps(rows, indent=1, default=str))
            else:
                for r in rows:
                    print(f"#{r['id']} {r['key']} v{r['version']} [{r['privacy']}] {r['body'][:120]!r}")
            return 0 if rows else 1
        if a.action == "resume":
            text = ledger.latest_resume()
            if text is None:
                print("no resume snapshot yet", file=sys.stderr)
                return 1
            print(text)
            return 0
        out = Path(a.out).resolve()
        root = EXPORT_DIR.resolve()
        if out != root and root not in out.parents:
            print(f"refused: export only writes under {EXPORT_DIR}/", file=sys.stderr)
            return 2
        for p in ledger.export(out):
            print(p)
        return 0
    except LedgerRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _add(ledger: Ledger, a: argparse.Namespace) -> int:
    fields = dict(kv.split("=", 1) for kv in a.field if "=" in kv)
    if len(fields) != len(a.field):
        print("--field needs NAME=VALUE", file=sys.stderr)
        return 2
    body = a.body if a.body is not None else (sys.stdin.read() if a.body_file == "-"
                                              else Path(a.body_file).read_text(encoding="utf-8"))
    if a.blob_file and Path(a.blob_file).stat().st_size > MAX_BLOB_FILE_BYTES:
        print(f"refused: --blob-file is larger than {MAX_BLOB_FILE_BYTES} bytes", file=sys.stderr)
        return 2
    blob = Path(a.blob_file).read_bytes() if a.blob_file else None
    try:
        entry = NewEntry(table=a.table, key=a.key, body=body, source=a.source,
                         privacy="private" if a.private else None, fields=fields, blob=blob)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(ledger.add(entry))
    return 0
