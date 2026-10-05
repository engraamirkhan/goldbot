"""Write the API's OpenAPI schema to web/openapi.json without starting a server.

The web client's types are generated from this file (`npm run gen:api` in web/), and CI regenerates both and
fails on any diff, so a backend contract change cannot ship without the frontend seeing it.

  python scripts/export_openapi.py [--out web/openapi.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.api.app import create_app  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "web" / "openapi.json"))
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as state:
        app = create_app(state, web_dist=Path(state) / "no-dist")
        schema = app.openapi()
    Path(args.out).write_text(json.dumps(schema, indent=1, sort_keys=True) + "\n")
    print("wrote", args.out, f"({len(schema['paths'])} paths)")


if __name__ == "__main__":
    main()
