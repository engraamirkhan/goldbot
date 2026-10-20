"""The split of goldbot/ops/run.py into goldbot/ops/services/ and goldbot/ops/commands/ (docs/process speed-up review
item 7) keeps run.py the public surface. tests/test_ops_run_cli_golden.py pins what the command line does; this file
pins the structure that keeps it so: the registry's names and explicit order, the re-exports, and the rule that moved
code reaches moved names through goldbot.ops.run (so `patch("goldbot.ops.run.x")` still takes effect).
"""
import ast
from pathlib import Path
from typing import Any

import pytest

import goldbot.ops.run as run
from goldbot.ops.commands import registry
from tests.test_ops_run_cli_golden import DISPATCH

ROOT = Path(__file__).resolve().parents[1]
MOVED_FILES = sorted((ROOT / "goldbot" / "ops" / "services").glob("*.py")) + \
    sorted((ROOT / "goldbot" / "ops" / "commands").glob("*.py"))

# every top-level name goldbot/ops/run.py defined before the split (and `main`, the dispatcher that replaced the
# if/elif chain under `if __name__ == "__main__"`)
BEFORE_SPLIT = (
    "SESSIONS_TIMEOUT_S", "SessionRefresher", "refresh_sessions", "_set_bridge_verdict", "apply_bridge_compat",
    "expire_bridge_compat", "begin_bridge_check", "engine_pass", "_record_tick_error", "SUP_ERR_LOG_S", "_SUP_ERR_LOGGED",
    "supervisor_pass", "run_supervisor", "refuse_start", "start_loop_watch", "run_engine", "_run_engine",
    "paper_stand_in", "engine_limits", "record_gate_cli", "costs_uploader", "publish_costs_cli", "drift_review_cli",
    "run_bridge", "run_api", "run_webhook", "_agent_runner", "_job_broker", "run_scheduler", "run_telegram",
    "run_news", "log",
)

# the old dispatcher's branches, in its order
ORDER = ("supervisor", "engine", "drift-review", "bridge", "api", "webhook", "scheduler", "telegram", "news",
         "record-gate", "gates", "gate-evidence", "publish-costs", "backup", "restore", "restore-drill", "setup",
         "preflight", "revoke-sessions", "mail-test", "owner-history", "health", "positions", "shadow-report", "ruin", "monthly-report",
         "perf-report", "sizing-feasibility", "bridge-smoke", "tv-import", "tv-repaint-check", "tv-pull", "ledger")


def test_the_registry_lists_every_command_in_the_old_dispatcher_order():
    names = [n for spec in registry.commands() for n in spec.names]
    assert names == list(ORDER)
    positions = [spec.pos for spec in registry.commands()]
    assert positions == sorted(set(positions))


def test_the_registry_knows_exactly_the_commands_the_golden_dispatch_covers():
    golden = {argv[0] for argv in DISPATCH.values() if argv and not argv[0].startswith("-")} - {"no-such-command"}
    assert set(ORDER) == golden
    assert all(registry.find(n) is not None for n in ORDER) and registry.find("no-such-command") is None


def test_every_name_the_run_module_had_is_still_exported_from_it():
    assert set(BEFORE_SPLIT) | {"main"} == set(run.__all__)
    for name in BEFORE_SPLIT:
        obj = getattr(run, name)
        home = getattr(obj, "__module__", None)
        if home is not None and name != "log":
            assert home.startswith(("goldbot.ops.services.", "goldbot.ops.commands.")), (name, home)
            assert getattr(__import__(home, fromlist=["_"]), name) is obj, name


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


@pytest.mark.parametrize("path", MOVED_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_moved_code_reaches_moved_names_through_the_run_module(path):
    """Inside a function body, a name run.py re-exports is used only as `resolve(name)` or `patched("name", name)`,
    so a test that patches goldbot.ops.run.<name> changes what the moved code calls, as before the split."""
    moved = set(run.__all__) - {"log", "main"}
    tree = ast.parse(path.read_text())
    parent = _parents(tree)
    bad: list[str] = []
    for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        for stmt in fn.body:
            for node in ast.walk(stmt):
                if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in moved):
                    continue
                call: Any = parent.get(node)
                ok = isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and node in call.args and (
                    (call.func.id == "resolve" and call.args == [node])
                    or (call.func.id == "patched" and len(call.args) == 2 and call.args[1] is node
                        and isinstance(call.args[0], ast.Constant) and call.args[0].value == node.id))
                if not ok:
                    bad.append(f"{path.name}:{node.lineno} {node.id}")
    assert not bad, bad


def test_patching_the_run_module_reaches_the_moved_callers(monkeypatch):
    seen: list[str] = []

    class Refresher:
        def __init__(self, source: Any) -> None:
            seen.append("refresher")
            self._thread = None

        def start(self, now: Any) -> bool:
            return False

        def running(self) -> bool:
            return False

        def collect(self, now: Any, current: Any) -> Any:
            return "table"
    monkeypatch.setattr(run, "SessionRefresher", Refresher)
    now: Any = None
    got: Any = run.refresh_sessions(object(), now, None)
    assert got == "table" and seen == ["refresher"]

    monkeypatch.setattr(run, "_record_tick_error", lambda eng, exc: seen.append(type(exc).__name__))

    class Broker:
        def last_tick(self, symbol: str) -> Any:
            raise TimeoutError

    class Eng:
        def no_quote_pass(self) -> None:
            seen.append("no_quote")
    assert run.engine_pass(Eng(), Broker(), "XAUUSD") is False
    assert seen == ["refresher", "TimeoutError", "no_quote"]


def test_a_taken_command_name_or_position_fails_at_registration(monkeypatch):
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))

    def handler(cmd: str, args: list[str]) -> None:
        return None
    with pytest.raises(ValueError, match="position 10 is taken"):
        registry.register_command("something-new", pos=10)(handler)
    with pytest.raises(ValueError, match="already registered"):
        registry.register_command("engine", pos=9999)(handler)
    with pytest.raises(ValueError, match="at least one name"):
        registry.register_command(pos=9998)
    registry.register_command("something-new", pos=9997)(handler)
    assert registry.find("something-new") is not None and registry.commands()[-1].names == ("something-new",)
