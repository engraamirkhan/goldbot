"""Copy the long daily history (scripts/long_daily_history.py output) to the brain, sha256-verified. Data never
enters git or a release (ADR 0048): the files go Mac raw/ -> brain state dir over ssh, nothing else.

Files: raw/daily/long/<key>_daily_long.parquet (one per instrument) and raw/daily/xauusd_daily_1978_present.parquet.
Next to them the copy writes
  manifest.json    per file: sha256, bytes, rows, first/last date, rows per source and the cut-over dates (the first
                   date each source contributes; a study that must not straddle a source switch reads these)
  manifest.sha256  `sha256sum -c` format, checked on the brain before anything is moved into place

Steps (idempotent, safe to re-run; a failed run leaves the previous files in place):
  1. stage the manifest locally and scp files + manifests (BatchMode) to a per-run inbox in the remote home;
  2. sudo install them into DEST/.incoming owned by the service user, `sha256sum -c` there; abort on any mismatch;
  3. rename each file into DEST (atomic per file), drop the inbox; 4. sha256sum DEST and compare with the Mac.

  uv run python scripts/ship_long_history.py --host goldbot-brain [--ssh-config ~/.ssh/goldbot.conf]
      [--dest /opt/goldbot/state/daily_history] [--owner goldbot] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.data.daily import DAILY_ROOT  # noqa: E402

GOLD_FILE = "xauusd_daily_1978_present.parquet"
DEFAULT_DEST = "/opt/goldbot/state/daily_history"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(root: Path = DAILY_ROOT) -> list[Path]:
    """Every long-history Parquet to ship, sorted by name; refuses an empty set."""
    files = sorted((root / "long").glob("*_daily_long.parquet"))
    gold = root / GOLD_FILE
    if gold.exists():
        files.append(gold)
    if not files:
        raise FileNotFoundError(f"no long-history Parquet under {root}")
    names = [f.name for f in files]
    if len(set(names)) != len(names):
        raise ValueError("duplicate file names: the destination is flat")
    return files


def cutovers(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Contiguous source segments in date order: the first/last date and rows each source contributes."""
    if df.empty:
        return []
    d = df.sort_values("date")
    seg = (d["source"] != d["source"].shift()).cumsum()
    out: list[dict[str, Any]] = []
    for _, g in d.groupby(seg, sort=True):
        out.append({"source": str(g["source"].iloc[0]), "from": f"{g['date'].iloc[0]:%Y-%m-%d}",
                    "to": f"{g['date'].iloc[-1]:%Y-%m-%d}", "rows": len(g)})
    return out


def file_entry(path: Path) -> dict[str, Any]:
    df = pd.read_parquet(path, columns=["date", "source", "available_utc"])
    return {"file": path.name, "sha256": sha256_file(path), "bytes": path.stat().st_size, "rows": len(df),
            "first": f"{df['date'].min():%Y-%m-%d}" if len(df) else None,
            "last": f"{df['date'].max():%Y-%m-%d}" if len(df) else None,
            "last_available_utc": str(pd.Timestamp(df["available_utc"].max())) if len(df) else None,
            "segments": cutovers(df)}


def build_manifest(files: Iterable[Path]) -> dict[str, Any]:
    entries = [file_entry(f) for f in files]
    return {"schema": "goldbot.long_daily_history/1", "files": entries,
            "note": "local research data, never published (ADR 0048); segments = source cut-overs"}


def sha_lines(manifest: dict[str, Any]) -> str:
    return "".join(f"{e['sha256']}  {e['file']}\n" for e in manifest["files"])


def parse_sha256sum(text: str) -> dict[str, str]:
    """`sha256sum` output -> {basename: sha}."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and len(parts[0]) == 64:
            out[Path(parts[1].lstrip("*")).name] = parts[0].lower()
    return out


def verify(manifest: dict[str, Any], remote: dict[str, str]) -> list[str]:
    """Every manifest file present remotely with the same sha256; returns the problems (empty = verified)."""
    problems: list[str] = []
    for e in manifest["files"]:
        got = remote.get(e["file"])
        if got is None:
            problems.append(f"missing {e['file']}")
        elif got != e["sha256"]:
            problems.append(f"sha256 mismatch {e['file']}: {got} != {e['sha256']}")
    return problems


def _run(cmd: list[str], dry: bool) -> str:
    print("+", " ".join(shlex.quote(c) for c in cmd), flush=True)
    if dry:
        return ""
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True, help="ssh alias of the brain (from the ssh config, never committed)")
    ap.add_argument("--ssh-config", type=Path, default=Path.home() / ".ssh" / "goldbot.conf")
    ap.add_argument("--dest", default=DEFAULT_DEST)
    ap.add_argument("--owner", default="goldbot", help="service user that owns the state dir")
    ap.add_argument("--root", type=Path, default=DAILY_ROOT)
    ap.add_argument("--stage", type=Path, default=Path.home() / "tmp" / "ship_long_history")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    files = collect(args.root)
    manifest = build_manifest(files)
    args.stage.mkdir(parents=True, exist_ok=True)
    (args.stage / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    (args.stage / "manifest.sha256").write_text(sha_lines(manifest))
    print(f"{len(files)} files, {sum(e['bytes'] for e in manifest['files']):,} bytes", flush=True)

    ssh = ["ssh", "-F", str(args.ssh_config), "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", args.host]
    inbox = f"gb_daily_history_inbox_{manifest['files'][0]['sha256'][:12]}"   # remote home, per content
    dest, inc, own = shlex.quote(args.dest), shlex.quote(args.dest + "/.incoming"), shlex.quote(args.owner)
    _run([*ssh, f"rm -rf {inbox} && mkdir -p {inbox}"], args.dry_run)
    _run(["scp", "-F", str(args.ssh_config), "-o", "BatchMode=yes", "-q", *map(str, files),
          str(args.stage / "manifest.json"), str(args.stage / "manifest.sha256"), f"{args.host}:{inbox}/"],
         args.dry_run)
    names = " ".join(shlex.quote(n) for n in [*(f.name for f in files), "manifest.json", "manifest.sha256"])
    _run([*ssh, " && ".join([
        f"sudo -n install -d -o {own} -g {own} -m 750 {dest}",
        f"sudo -n rm -rf {inc}",
        f"sudo -n install -d -o {own} -g {own} -m 750 {inc}",
        f"sudo -n install -o {own} -g {own} -m 640 $(for n in {names}; do printf '%s/%s ' {inbox} \"$n\"; done) {inc}/",
        f"sudo -n sh -c 'cd {inc} && sha256sum -c --quiet manifest.sha256'",
        f"sudo -n sh -c 'cd {inc} && for n in {names}; do mv -f \"$n\" {dest}/\"$n\"; done && rmdir {inc}'",
        f"rm -rf {inbox}"])], args.dry_run)
    out = _run([*ssh, f"sudo -n sh -c 'cd {dest} && sha256sum *.parquet'"], args.dry_run)
    if args.dry_run:
        return 0
    problems = verify(manifest, parse_sha256sum(out))
    for p in problems:
        print("FAIL", p)
    print(f"verified {len(manifest['files']) - len(problems)}/{len(manifest['files'])} files on {args.host}:{args.dest}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
