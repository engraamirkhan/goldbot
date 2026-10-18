"""`publish-costs [--out FILE]`: the canonical broker's measured costs -> release costs-v1. Moved verbatim from
goldbot/ops/run.py, which re-exports `costs_uploader` and `publish_costs_cli` (the scheduler uses the uploader too)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from goldbot.ops.commands.registry import register_command
from goldbot.ops.services._base import resolve

if TYPE_CHECKING:   # imports are lazy; this is for annotations only
    from goldbot.ops.accounts import Account


def costs_uploader(token: str) -> Callable[[bytes], str]:
    """Upload the published cost table to release costs-v1 as costs_measured.json with the github-token."""
    from goldbot.data.release import upload_asset
    from goldbot.execution.costs import COSTS_ASSET, COSTS_TAG
    return lambda data: upload_asset(
        token, COSTS_TAG, COSTS_ASSET, data, title="Measured broker costs",
        notes="costs_measured.json: the canonical broker's measured spread, slippage, commission and swap (costs only), "
              "published by the VPS after nightly_costs. research.yml uses it; without it research charges the priors.")


def publish_costs_cli(argv: list[str], accounts: list[Account] | None = None, state_dir: str | Path = "state",
                      upload: Callable[[bytes], str] | None = None) -> int:
    """publish-costs [--out FILE]: publish the canonical-cost broker's nightly cost table (costs only, no account id,
    login, balance or equity) to release costs-v1 with the keyring's github-token, as nightly_costs does when
    `costs.publish_release` is on. `--out FILE` writes the same JSON locally instead (no upload; never commit it).
    Exit 1 when refused (no table, swap or spread unmeasured) or the upload fails."""
    import argparse

    import pandas as pd

    from goldbot.config import load_settings
    from goldbot.execution.costs import publishable
    from goldbot.ops import accounts as acc_mod
    from goldbot.ops.jobs import canonical_cost_table, publish_costs
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run publish-costs")
    ap.add_argument("--out", default="", help="write the published JSON here instead of uploading it")
    args = ap.parse_args(argv)
    settings = load_settings()
    accs = accounts if accounts is not None else list(acc_mod.load_accounts().values())
    if args.out:
        found = canonical_cost_table(settings, accs, Path(state_dir))
        pub, reason = (None, "no cost table on the canonical-cost broker yet: run the scheduler's nightly_costs first") \
            if found is None else publishable(found[0], found[1].broker)
        if pub is None:
            print(f"refused: {reason}")
            return 1
        Path(args.out).write_text(pub.model_dump_json(indent=1))
        print(f"wrote {args.out}: {pub.broker} measured {pub.measured_at:%Y-%m-%d %H:%M} UTC, "
              + ", ".join(f"{k} {v.source}" for k, v in pub.fields.items()))
        return 0
    if upload is None:
        token = acc_mod.get_secret("github-token")
        if not token:
            print("no github-token in the keyring: python -m goldbot.ops.accounts set github-token")
            return 1
        upload = resolve(costs_uploader)(token)
    res = publish_costs(settings, accs, Path(state_dir), upload, pd.Timestamp.now("UTC"))
    print(("published " + str(res.get("url", ""))) if res["published"] else f"refused: {res['reason']}")
    return 0 if res["published"] else 1


@register_command("publish-costs", pos=130)
def _publish_costs(cmd: str, args: list[str]) -> None:
    sys.exit(resolve(publish_costs_cli)(args))
