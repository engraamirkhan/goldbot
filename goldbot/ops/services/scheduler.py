"""The scheduler service (`python -m goldbot.ops.run scheduler`): nightly jobs, retrains, the staff agents and the
read-only brokers the jobs use.

Moved verbatim from goldbot/ops/run.py, which re-exports every name; calls between moved names go through
goldbot.ops.run (`resolve`, goldbot/ops/services/_base.py) so patching there still takes effect.
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Callable

from goldbot.ops.services._base import log, resolve

if TYPE_CHECKING:   # service entry points import lazily; these are for annotations only
    from goldbot.agents.runner import AgentRunner
    from goldbot.config import Settings
    from goldbot.data.store import Store


def _agent_runner(settings: Settings, store: Store,
                  trial_runner: Callable[[str, dict[str, Any], str], dict[str, Any]] | None = None) -> AgentRunner | None:
    """Staff agents run only when the owner has stored an Anthropic API key in the OS keyring."""
    from pathlib import Path

    from goldbot.agents.runner import AgentRunner, SpendLedger  # noqa: F811
    from goldbot.agents.tools import ReadOnlyTools
    from goldbot.ops import accounts
    key = accounts.get_secret("anthropic-api-key")
    if not key:
        log.warning("no anthropic-api-key in the keyring: staff agents are off "
                    "(python -m goldbot.ops.accounts set anthropic-api-key)")
        return None
    import anthropic
    return AgentRunner(anthropic.Anthropic(api_key=key), ReadOnlyTools("state", store, trial_runner=trial_runner),
                       SpendLedger(Path("state") / "agent_spend.json", settings.agents.monthly_cap_usd), "state",
                       model=settings.agents.model)


def _job_broker(acc: Any) -> Any:
    """A read-only Broker for the scheduler's feed_reconcile (D12) and broker_ledger (GAP-07/08): the account's bridge
    when one is configured, else on Windows the terminal's own saved login (attach without logging in, as the bridge
    does); None elsewhere. Only read-only methods are called on it: get_bars, symbol_info, account, positions,
    deals_since and copy_ticks (tick_record, BACKLOG 79; all retry-safe bridge methods; never an order)."""
    from goldbot.ops import accounts
    bridge = accounts.bridge_endpoint(acc.account_id)
    if bridge is not None:
        from goldbot.execution.bridge import RemoteBroker
        return RemoteBroker(bridge[0], bridge[1], name=f"mt5-remote-{acc.account_id}")
    if sys.platform == "win32":
        from goldbot.execution.mt5_adapter import MT5Broker
        return MT5Broker(terminal_path=acc.terminal_path, login=None, password=None, server=acc.server,
                         server_tz=acc.server_tz, symbol=acc.symbol, account_label=acc.account_id)
    return None


def run_scheduler() -> None:
    from pathlib import Path

    from goldbot.config import load_settings
    from goldbot.data.release import sync_release
    from goldbot.data.store import Store
    from goldbot.ops import accounts
    from goldbot.ops.commands.costs import costs_uploader
    from goldbot.ops.jobs import JobContext, build_scheduler, make_trial_runner
    from goldbot.research.model_registry import ModelRegistry
    from goldbot.research.population import Population
    from goldbot.research.registry import TrialRegistry
    from goldbot.research.registry_sync import sync as sync_registry
    settings = load_settings()
    # GitHub token for the trial-registry release copy: `python -m goldbot.ops.accounts set github-token` (keyring).
    # Without it the VPS registry stays local and the HANDOFF note about one registry applies.
    gh_token = accounts.get_secret("github-token")
    if not gh_token:
        log.warning("no github-token in the keyring: trial registry will not be shared with the research workflow")
    ctx = JobContext(settings=settings, store=Store(settings.data_root), state_dir=Path("state"),
                     models=ModelRegistry(settings.research.models_dir), trials=TrialRegistry(settings.research.registry),
                     accounts=accounts.enabled_accounts(),   # live accounts only once the phase gate has passed
                     sync_bars=lambda store: sync_release(store, token=gh_token),   # bars (data-v1) + macro (macro-v1)
                     sync_trials=(lambda path: sync_registry(path, gh_token)) if gh_token else None,
                     population=Population(Path("state") / "population.json"),
                     broker_for=resolve(_job_broker))   # feed_reconcile (D12) and broker_ledger (GAP-07/08), read-only
    if settings.costs.publish_release:          # measured costs for research.yml (release costs-v1)
        if gh_token:
            ctx.upload_costs = resolve(costs_uploader)(gh_token)
        else:
            log.warning("costs.publish_release is on but there is no github-token: the cost table is not published")
    ctx.agent_runner = resolve(_agent_runner)(settings, ctx.store, trial_runner=make_trial_runner(ctx))
    from goldbot.data.econ_calendar import fetch_ff_week
    ctx.fetch_calendar = fetch_ff_week
    from goldbot.ops.health import start_heartbeat
    start_heartbeat("state", "scheduler")         # a thread: a retrain blocks the loop for hours
    sch = build_scheduler(ctx)
    for name, st in sch.status()["jobs"].items():
        log.info("scheduler: %s next at %s", name, st["next_slot"])
    sch.run_forever()
