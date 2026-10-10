"""Service entry points used by NSSM on the VPS and by hand in development.

  python -m goldbot.ops.run supervisor
  python -m goldbot.ops.run engine <account_id>
  python -m goldbot.ops.run api
  python -m goldbot.ops.run webhook
  python -m goldbot.ops.run scheduler
  python -m goldbot.ops.run telegram
  python -m goldbot.ops.run news
  python -m goldbot.ops.run record-gate <gate_name> --evidence <path or text>   # appends to state/phase_state.json
  python -m goldbot.ops.run health [--json] [--static] [--out FILE] [--baseline FILE]   (exit 1 on a fail)
  python -m goldbot.ops.run export-costs --out config/costs_measured.json   # canonical broker's measured cost table
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:   # service entry points import lazily; these are for annotations only
    from goldbot.agents.runner import AgentRunner
    from goldbot.config import Settings
    from goldbot.data.store import Store
    from goldbot.ops.accounts import Account
    from goldbot.risk import RiskLimits

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("goldbot.run")


def run_supervisor() -> None:
    from goldbot.config import load_settings
    from goldbot.risk.supervisor import Supervisor, SupervisorLimits
    sup = Supervisor("state", SupervisorLimits.from_settings(load_settings().risk))
    while True:
        st = sup.evaluate()
        if st["halt"]:
            log.warning("HALT %s", st["reasons"])
        time.sleep(5)


def run_engine(account_id: str) -> None:
    from pathlib import Path

    import pandas as pd

    from goldbot.config import load_settings
    from goldbot.engine import Engine, EngineConfig
    from goldbot.ops import accounts
    from goldbot.research.model_registry import ModelRegistry
    from goldbot.research.population import Population
    from goldbot.telegram.approvals import ApprovalCenter
    from goldbot.telegram.bus import ApprovalBus
    acc = accounts.load_accounts()[account_id]
    settings = load_settings()
    if acc.is_live and acc not in accounts.enabled_accounts("live"):
        raise SystemExit("live account not unlocked by the phase gate")
    limits = engine_limits(settings, acc)
    bridge = accounts.bridge_endpoint(account_id)
    if bridge is not None:
        # terminal on another machine (Oracle free tier: MT5 under Wine); a bridge failure ends this process and the
        # service manager restarts it, so restart reconciliation settles any order whose result was lost (row X7)
        from goldbot.execution.bridge import RemoteBroker
        broker: Any = RemoteBroker(bridge[0], bridge[1], name=f"mt5-remote-{account_id}")
        why = accounts.verify_terminal_account(acc, broker.account())
        if why:
            raise SystemExit(f"refusing to trade {account_id} through the bridge: {why}")
    elif sys.platform == "win32":
        from goldbot.execution.mt5_adapter import MT5Broker
        acc = accounts.ensure_login(acc)
        broker = MT5Broker(terminal_path=acc.terminal_path, login=acc.login, password=accounts.account_password(acc),
                           server=acc.server, server_tz=acc.server_tz, symbol=acc.symbol, account_label=account_id)
        why = accounts.verify_terminal_account(acc, broker.account())
        if why:
            raise SystemExit(f"refusing to trade {account_id}: {why}")
    else:
        from goldbot.execution.paper import PaperBroker
        broker = PaperBroker(symbol=acc.symbol)
        log.warning("not on Windows: running %s against the paper broker", account_id)
    # proposals go to the approval bus in state/; the dashboard and the Telegram service decide through it
    center = ApprovalCenter(set(settings.telegram.allowed_user_ids), bus=ApprovalBus("state"))
    registry_file = Path(settings.research.models_dir) / "registry.json"

    population_file = Path("state") / "population.json"

    def champions() -> dict:
        # no champion for an agent -> no model -> the engine never proposes for it (no placeholder probabilities)
        return ModelRegistry(settings.research.models_dir).champion_models()

    def shadow_set() -> dict:
        return ModelRegistry(settings.research.models_dir).shadow_models()

    def population_view() -> tuple[list, dict[str, float]]:
        """Agents in the shadow book (live, shadow, recently retired) and the live agents' capital shares."""
        pop = Population(population_file)
        pop.ensure_founders(pd.Timestamp.now("UTC"))
        members = pop.in_book()
        return [m.specialist() for m in members], {m.agent_id: m.capital_weight for m in members if m.status == "live"}

    broker_cfg = settings.brokers.get(acc.broker)
    shadow_host = bool(broker_cfg and broker_cfg.canonical_costs)   # one shadow book: the canonical-cost broker

    agents, live_shares = population_view()
    eng = Engine(EngineConfig(account_id=account_id, broker_name=acc.broker, mode=acc.mode, approval_mode="propose", symbol=acc.symbol,
                              magic_base=acc.magic_base, server_tz=acc.server_tz, state_dir="state", data_root=settings.data_root,
                              shadow_host=shadow_host, halt_checks=True, news_blackout=True,
                              blackout_before_min=settings.risk.blackout.before_min,
                              blackout_after_min=settings.risk.blackout.after_min,
                              shock_blackout_min=settings.news.shock_blackout_min,
                              shock_min_relevance=settings.news.shock_min_relevance, live_clock=True), broker, agents,
                 champions(), center, limits=limits, shadow_models=shadow_set() if shadow_host else None,
                 live_shares=live_shares)
    warm = eng.warm_start(pd.Timestamp.now("UTC"))
    log.info("engine %s started (%s), models: %s, %d 1m bars of history, risk per trade %.4f", account_id,
             type(broker).__name__, sorted(eng.models), warm, limits.risk_per_trade)
    def mtimes() -> tuple[float, ...]:
        return tuple(f.stat().st_mtime if f.exists() else -1.0 for f in (registry_file, population_file))

    seen = mtimes()
    last_check = time.time()
    while True:
        try:
            t = broker.last_tick(acc.symbol)
            eng.on_tick(t)
        except AssertionError:
            pass
        if time.time() - last_check > 60:          # promotions by the scheduler take effect without a restart
            last_check = time.time()
            now_m = mtimes()
            if now_m != seen:
                seen = now_m
                try:
                    agents, live_shares = population_view()
                    eng.agents = {a.agent_id: a for a in agents}
                    eng.live_shares = live_shares
                    eng.models = champions()
                    if shadow_host:
                        eng.set_shadow_models(shadow_set())
                    log.info("reloaded: %d agents, %d live, models %s, shadow %s", len(eng.agents), len(live_shares),
                             sorted(eng.models), sorted(eng.shadow_models))
                except (ValueError, TypeError, OSError) as exc:
                    log.error("model reload refused, keeping current models: %s", exc)
        time.sleep(0.25)


def engine_limits(settings: Settings, acc: Account) -> RiskLimits:
    """RiskLimits from settings.yaml `risk:`; a live account before the full-size gate (or with an unreadable phase
    file) gets the tiny-live risk per trade."""
    from goldbot.ops import accounts
    from goldbot.risk import RiskLimits
    return RiskLimits.from_settings(settings.risk, tiny_live=accounts.tiny_live_risk(acc))


def record_gate_cli(argv: list[str]) -> int:
    """record-gate <gate_name> --evidence <path or text>"""
    import argparse

    from goldbot.ops import accounts
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run record-gate")
    ap.add_argument("gate", help="one of: " + ", ".join(accounts.GATES))
    ap.add_argument("--evidence", required=True, help="path to the evidence file (hashed) or a short text")
    args = ap.parse_args(argv)
    try:
        st = accounts.record_gate(args.gate, args.evidence)
    except ValueError as exc:
        print(f"refused: {exc}")
        return 1
    print(f"recorded {args.gate}: phase {st['phase']}, gates passed {st['gates_passed']}")
    return 0


def export_costs_cli(argv: list[str], accounts: list[Account] | None = None, state_dir: str | Path = "state") -> int:
    """export-costs --out FILE: the canonical-cost broker's nightly cost table (state/costs_<account>.json, with the
    terminal's swap and commission) copied to FILE, for `scripts/research_pass.py --cost-table` on GitHub (commit it
    as config/costs_measured.json; research.yml passes it when present). Exit 1 when no table exists yet."""
    import argparse

    from goldbot.config import load_settings
    from goldbot.ops import accounts as acc_mod
    from goldbot.ops.jobs import canonical_cost_table
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run export-costs")
    ap.add_argument("--out", required=True, help="where to write the cost table JSON")
    args = ap.parse_args(argv)
    accs = accounts if accounts is not None else list(acc_mod.load_accounts().values())
    found = canonical_cost_table(load_settings(), accs, Path(state_dir))
    if found is None:
        print("no cost table for an account on the canonical-cost broker yet: run the scheduler's nightly_costs first")
        return 1
    table, acc = found
    table.save(args.out)
    swap = ("swap measured: long {:+.2f} / short {:+.2f} USD per lot per night".format(
        table.swap_long_usd_per_lot, table.swap_short_usd_per_lot)
        if table.swap_long_usd_per_lot is not None and table.swap_short_usd_per_lot is not None
        else "WARNING: no measured swap (research will charge the settings prior)")
    print(f"wrote {args.out}: {acc.account_id} built {table.built_utc:%Y-%m-%d %H:%M} UTC, commission "
          f"{table.commission_per_lot_side_usd:.2f} USD per lot per side "
          f"({'measured' if table.commission_measured else 'settings'}), {swap}")
    return 0


def run_bridge(account_id: str) -> None:  # pragma: no cover - needs the MetaTrader5 package and a terminal
    """Serve the MT5 terminal on this machine to the engine (goldbot/execution/bridge.py). Run under the Wine (or
    Windows) Python next to the terminal. The terminal's saved login is used; the listen address and token come from
    the keyring (`mt5-bridge-listen-<account>` as host:port, `mt5-bridge-token-<account>`)."""
    from goldbot.execution.bridge import GuardedBroker, serve
    from goldbot.execution.mt5_adapter import MT5Broker
    from goldbot.ops import accounts
    acc = accounts.load_accounts()[account_id]
    listen = accounts.get_secret(f"mt5-bridge-listen-{account_id}")
    token = accounts.get_secret(accounts.bridge_token_key(account_id))
    if not listen or not token:
        raise SystemExit(f"store mt5-bridge-listen-{account_id} and {accounts.bridge_token_key(account_id)} first "
                         "(python -m goldbot.ops.accounts bridge-serve " + account_id + ")")
    host, port = listen.rsplit(":", 1)
    broker = MT5Broker(terminal_path=acc.terminal_path, login=None, password=None, server=acc.server,
                       server_tz=acc.server_tz, symbol=acc.symbol, account_label=account_id)
    why = accounts.verify_terminal_account(acc, broker.account())
    if why:                                        # e.g. the terminal's saved login is a live account
        broker.shutdown()
        raise SystemExit(f"refusing to serve {account_id}: {why}")
    serve(GuardedBroker(broker, magic_base=acc.magic_base), host, int(port), token)


def run_api() -> None:
    import uvicorn

    from goldbot.api.app import create_app
    uvicorn.run(create_app("state"), host="127.0.0.1", port=8787)


def run_webhook() -> None:
    import uvicorn

    from goldbot.ops import accounts
    from goldbot.webhook.app import create_app
    secret = accounts.get_credential("tradingview-webhook-secret", "TradingView webhook shared secret")
    uvicorn.run(create_app(secret), host="0.0.0.0", port=8443)


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


def run_scheduler() -> None:
    from pathlib import Path

    from goldbot.config import load_settings
    from goldbot.data.release import sync_release_bars
    from goldbot.data.store import Store
    from goldbot.ops import accounts
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
                     sync_bars=lambda store: sync_release_bars(store, token=gh_token),
                     sync_trials=(lambda path: sync_registry(path, gh_token)) if gh_token else None,
                     population=Population(Path("state") / "population.json"))
    ctx.agent_runner = _agent_runner(settings, ctx.store, trial_runner=make_trial_runner(ctx))
    from goldbot.data.econ_calendar import fetch_ff_week
    ctx.fetch_calendar = fetch_ff_week
    sch = build_scheduler(ctx)
    for name, st in sch.status()["jobs"].items():
        log.info("scheduler: %s next at %s", name, st["next_slot"])
    sch.run_forever()


def run_telegram() -> None:
    from goldbot.config import load_settings
    from goldbot.ops import accounts
    from goldbot.telegram.bot import TelegramBot
    settings = load_settings()
    token = accounts.get_secret("telegram-bot-token")
    if not token:
        raise SystemExit("no telegram-bot-token in the keyring: python -m goldbot.ops.accounts set telegram-bot-token")
    if not settings.telegram.allowed_user_ids:
        raise SystemExit("settings.yaml telegram.allowed_user_ids is empty: add your Telegram user id")
    TelegramBot(token, "state", set(settings.telegram.allowed_user_ids)).run()


def run_news() -> None:
    import time
    from pathlib import Path

    import pandas as pd

    from goldbot.agents.runner import SpendLedger
    from goldbot.config import load_settings
    from goldbot.data.news import fetch
    from goldbot.data.news_collector import NewsCollector
    from goldbot.data.store import Store
    from goldbot.ops import accounts
    settings = load_settings()
    key = accounts.get_secret("anthropic-api-key")
    client = None
    if key:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
    else:
        log.warning("no anthropic-api-key in the keyring: headlines are collected but not scored (no shock blackout)")
    collector = NewsCollector(Store(settings.data_root), "state", settings.news.feeds, fetch, client,
                              SpendLedger(Path("state") / "agent_spend.json", settings.agents.monthly_cap_usd),
                              settings.news.model, settings.news.daily_cap_usd)
    while True:
        try:
            log.info("news: %s", collector.poll(pd.Timestamp.now("UTC")))
        except Exception:
            log.exception("news poll")
        time.sleep(settings.news.poll_seconds)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "supervisor":
        run_supervisor()
    elif cmd == "engine":
        run_engine(sys.argv[2])
    elif cmd == "bridge":
        run_bridge(sys.argv[2])
    elif cmd == "api":
        run_api()
    elif cmd == "webhook":
        run_webhook()
    elif cmd == "scheduler":
        run_scheduler()
    elif cmd == "telegram":
        run_telegram()
    elif cmd == "news":
        run_news()
    elif cmd == "record-gate":
        sys.exit(record_gate_cli(sys.argv[2:]))
    elif cmd == "export-costs":
        sys.exit(export_costs_cli(sys.argv[2:]))
    elif cmd == "health":
        from goldbot.ops.health import main as health_main
        sys.exit(health_main(sys.argv[2:]))
    else:
        print(__doc__)
        sys.exit(1)
