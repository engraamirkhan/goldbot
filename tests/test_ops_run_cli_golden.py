"""The command line of `python -m goldbot.ops.run` (the "goldbot run" every service and runbook calls) is pinned here.

The split of goldbot/ops/run.py into goldbot/ops/services/ and goldbot/ops/commands/ is a pure move (docs/process
speed-up review item 7), so everything an operator or a service manager can observe stays the same:

* the usage text printed for no command, `--help` or an unknown command, and its exit code;
* every subcommand's `--help` (only those whose `--help` is side-effect free: the services would start, `backup` and
  `restore-drill` would run a backup, `mail-test` would send an email);
* which function each command reaches, with which arguments (captured in process with a trace hook that stops at the
  first call into a known entry function, so nothing runs), and the error a missing argument gives;
* the invocations in the systemd units, the PowerShell bootstrap/update scripts and the deploy scripts;
* importing goldbot.ops.run loads no third-party package (the MT5 bridge's Wine Python installs a short, hash-pinned
  list: scripts/bridge_requirements.py).

tests/data/ops_run_cli_golden.json was written from the hand-written dispatcher before the split. Regenerate it only for
an intended change of the command line:  GOLDBOT_WRITE_RUN_GOLDEN=1 pytest tests/test_ops_run_cli_golden.py
(each test rewrites its own section).
"""
from __future__ import annotations

import io
import json
import os
import re
import runpy
import subprocess
import sys
import warnings
from collections.abc import Callable
from contextlib import redirect_stdout
from pathlib import Path
from types import FrameType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).parent / "data" / "ops_run_cli_golden.json"
WRITE = os.environ.get("GOLDBOT_WRITE_RUN_GOLDEN") == "1"

# subcommands whose --help only prints (argparse) or, for drift-review, only reads state/drift.json in the cwd
HELP_COMMANDS = ("record-gate", "gates", "gate-evidence", "publish-costs", "restore", "setup", "preflight",
                 "revoke-sessions", "owner-history", "health", "ruin", "monthly-report", "perf-report",
                 "sizing-feasibility", "drift-review", "bridge-smoke", "tv-import", "tv-repaint-check", "tv-pull", "ledger")

# every command the dispatcher knows, with sample arguments (the trace stops before anything runs)
DISPATCH: dict[str, list[str]] = {
    "supervisor": ["supervisor"],
    "engine": ["engine", "icm-demo"],
    "engine-no-account": ["engine"],
    "drift-review": ["drift-review", "--clear", "checked it"],
    "bridge": ["bridge", "icm-demo"],
    "bridge-no-account": ["bridge"],
    "api": ["api"],
    "webhook": ["webhook"],
    "scheduler": ["scheduler"],
    "telegram": ["telegram"],
    "news": ["news"],
    "record-gate": ["record-gate", "paper_to_tiny_live", "--evidence", "e.txt"],
    "gates": ["gates", "--json"],
    "gate-evidence": ["gate-evidence", "leakage_audit", "--passed", "yes"],
    "publish-costs": ["publish-costs", "--out", "c.json"],
    "backup": ["backup", "--init"],
    "restore": ["restore", "--latest", "--to", "d"],
    "restore-drill": ["restore-drill"],
    "setup": ["setup", "--account", "icm-demo"],
    "preflight": ["preflight", "--account", "icm-demo"],
    "revoke-sessions": ["revoke-sessions", "--state", "s"],
    "mail-test": ["mail-test", "ignored"],
    "owner-history": ["owner-history", "h.html"],
    "health": ["health", "--static"],
    "ruin": ["ruin", "--trades-per-week", "5"],
    "monthly-report": ["monthly-report", "--json"],
    "perf-report": ["perf-report", "--json"],
    "sizing-feasibility": ["sizing-feasibility", "--tiny-live"],
    "tv-import": ["tv-import", "e.csv", "--indicator", "K", "--version", "1", "--tf", "15"],
    "tv-repaint-check": ["tv-repaint-check", "--indicator", "K", "--version", "1", "--tf", "15"],
    "tv-pull": ["tv-pull", "status"],
    "ledger": ["ledger", "resume"],
    "bridge-smoke": ["bridge-smoke", "icm-demo"],
    "no-command": [],
    "help": ["--help"],
    "unknown": ["no-such-command"],
}

# the run module's own functions a command reaches (the dispatcher's targets; helpers they call are not entries)
RUN_ENTRIES = ("run_supervisor", "run_engine", "drift_review_cli", "run_bridge", "run_api", "run_webhook",
               "run_scheduler", "run_telegram", "run_news", "record_gate_cli", "publish_costs_cli")

# files that start goldbot services or call the CLI on a server
ENTRY_FILES = ("goldbot/ops/linux/**/*", "goldbot/ops/*.ps1", "scripts/**/*")
INVOKE = re.compile(r"-m goldbot\.ops\.run\b([^\n\"'`|>;)]*)")


def _golden() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}


def _check(section: str, got: Any) -> None:
    """Compare `got` with the golden section; with GOLDBOT_WRITE_RUN_GOLDEN=1 write it instead."""
    if WRITE:
        data = _golden()
        data[section] = got
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
        return
    assert GOLDEN.exists(), f"{GOLDEN} missing: GOLDBOT_WRITE_RUN_GOLDEN=1 pytest {Path(__file__).name}"
    assert got == _golden()[section]


def _cli(args: list[str], cwd: Path) -> dict[str, Any]:
    env = dict(os.environ, COLUMNS="100", NO_COLOR="1", PYTHONPATH=str(ROOT))
    env.pop("FORCE_COLOR", None)
    p = subprocess.run([sys.executable, "-m", "goldbot.ops.run", *args], cwd=cwd, env=env, capture_output=True,
                       text=True, timeout=120)
    return {"rc": p.returncode, "stdout": p.stdout}


ADDRESS = re.compile(r" at 0x[0-9a-f]+")


def _same_help(got: dict[str, Any], want: dict[str, Any]) -> bool:
    """Exact, except that a help text showing an object address (sizing-feasibility: argparse reads its "8% stage" as
    a %-format and prints the action's dict) is compared without the address and the line wrapping it shifts."""
    def flat(text: str) -> str:
        return " ".join(text.split())
    if got["rc"] != want["rc"]:
        return False
    if not ADDRESS.search(flat(want["stdout"])):
        return bool(got["stdout"] == want["stdout"])
    return ADDRESS.sub("", flat(got["stdout"])) == ADDRESS.sub("", flat(want["stdout"]))


def test_the_usage_text_and_its_exit_code_are_unchanged(tmp_path):
    got = {name: _cli(args, tmp_path) for name, args in (("no-command", []), ("help", ["--help"]),
                                                          ("unknown", ["no-such-command"]))}
    _check("usage", got)


@pytest.mark.parametrize("cmd", HELP_COMMANDS)
def test_every_subcommand_help_is_unchanged(tmp_path, cmd):
    got = _cli([cmd, "--help"], tmp_path)
    if WRITE:
        data = _golden().get("help", {})
        data[cmd] = got
        _check("help", data)
        return
    want = _golden()["help"][cmd]
    assert _same_help(got, want), (got, want)


def _entry_functions() -> dict[tuple[str, int, str], str]:
    """(file, first line, name) of every function a command may reach -> its label. The run module's own functions are
    labelled `goldbot.ops.run:<name>` wherever they are defined, so the label survives the move."""
    import goldbot.ops.run as run_mod
    from goldbot.api import auth, mailer
    from goldbot.data import tv_csv
    from goldbot.ops import (
        backup,
        gates_phase,
        health,
        monthly_report,
        perf_report,
        preflight,
        setup_wizard,
        smoke,
    )
    from goldbot.research import min_lot, owner_history, ruin, tv_repaint
    out: dict[tuple[str, int, str], str] = {}

    def add(fn: Callable[..., Any], label: str) -> None:
        c = fn.__code__
        out[(c.co_filename, c.co_firstlineno, c.co_name)] = label
    for name in RUN_ENTRIES:
        add(getattr(run_mod, name), f"goldbot.ops.run:{name}")
    for fn in (gates_phase.main, gates_phase.evidence_main, backup.main, setup_wizard.main, preflight.main,
               auth.revoke_sessions_cli, mailer.main, owner_history.main, health.main, ruin.main, monthly_report.main,
               perf_report.main, min_lot.main, smoke.main, tv_csv.main, tv_repaint.main):
        add(fn, f"{fn.__module__}:{fn.__qualname__}")
    return out


class _Reached(Exception):
    pass


def _plain(v: Any) -> Any:
    """An argument as JSON: a function default (backup's `runner`) by its name, not its address."""
    return f"<{v.__module__}.{v.__qualname__}>" if callable(v) else v


def _dispatch(argv: list[str], stop: bool = True) -> dict[str, Any]:
    """Run goldbot.ops.run as __main__ with `argv` and stop at the first call into an entry function (`stop=False`: no
    trace, run to the end; the caller has replaced the entry functions)."""
    targets = _entry_functions() if stop else {}
    hit: dict[str, Any] = {}

    def trace(frame: FrameType, event: str, arg: Any) -> None:
        if event == "call":
            c = frame.f_code
            label = targets.get((c.co_filename, c.co_firstlineno, c.co_name))
            if label is not None:
                hit.update(target=label, args={k: _plain(v) for k, v in frame.f_locals.items()})
                raise _Reached
        return None
    old_argv, old_trace = sys.argv, sys.gettrace()
    sys.argv = [str(ROOT / "goldbot" / "ops" / "run.py"), *argv]
    out = io.StringIO()
    try:
        with warnings.catch_warnings(), redirect_stdout(out):
            warnings.simplefilter("ignore", RuntimeWarning)       # "found in sys.modules": run as __main__ on purpose
            if stop:
                sys.settrace(trace)
            try:
                runpy.run_module("goldbot.ops.run", run_name="__main__")
                hit["returned"] = True
            except _Reached:
                pass
            except SystemExit as exc:
                hit["exit"] = exc.code
            except Exception as exc:
                hit["error"] = type(exc).__name__
            finally:
                sys.settrace(old_trace)
    finally:
        sys.argv = old_argv
    if "exit" in hit:
        hit["printed_usage"] = out.getvalue().startswith("Service entry points")
    return hit


def test_every_command_reaches_the_same_function_with_the_same_arguments():
    _check("dispatch", {name: _dispatch(argv) for name, argv in DISPATCH.items()})


# One-shot commands: the process exit code is the entry function's return value (`sys.exit(fn(...))`). Operators and
# scripts act on it: vps_update.ps1 rolls back on `health`'s exit code, preflight blocks a start, gates/record-gate
# refuse. Each maps to the entry function the golden dispatch names (module, attribute).
ONE_SHOT: dict[str, tuple[str, str]] = {
    "drift-review": ("goldbot.ops.run", "drift_review_cli"),
    "record-gate": ("goldbot.ops.run", "record_gate_cli"),
    "gates": ("goldbot.ops.gates_phase", "main"),
    "gate-evidence": ("goldbot.ops.gates_phase", "evidence_main"),
    "publish-costs": ("goldbot.ops.run", "publish_costs_cli"),
    "backup": ("goldbot.ops.backup", "main"),
    "restore": ("goldbot.ops.backup", "main"),
    "restore-drill": ("goldbot.ops.backup", "main"),
    "setup": ("goldbot.ops.setup_wizard", "main"),
    "preflight": ("goldbot.ops.preflight", "main"),
    "revoke-sessions": ("goldbot.api.auth", "revoke_sessions_cli"),
    "mail-test": ("goldbot.api.mailer", "main"),
    "owner-history": ("goldbot.research.owner_history", "main"),
    "health": ("goldbot.ops.health", "main"),
    "ruin": ("goldbot.research.ruin", "main"),
    "monthly-report": ("goldbot.ops.monthly_report", "main"),
    "perf-report": ("goldbot.ops.perf_report", "main"),
    "sizing-feasibility": ("goldbot.research.min_lot", "main"),
    "bridge-smoke": ("goldbot.ops.smoke", "main"),
    "tv-import": ("goldbot.data.tv_csv", "main"),
    "tv-repaint-check": ("goldbot.research.tv_repaint", "main"),
    "ledger": ("goldbot.ledger.cli", "main"),
    "tv-pull": ("goldbot.data.tv_pull", "main"),             # `follow` loops, but exits with main()'s value
}
# Services are exempt from the exit-code rule: each loops forever (supervisor, engine, scheduler, telegram, news) or
# serves until stopped (api and webhook under uvicorn, bridge under serve), and the dispatcher never passed a return
# value to sys.exit for them; their exit code is the process's own (0 on return, non-zero on a refusal's SystemExit or
# an uncaught error). Pinned the other way round: a value a service returns is NOT turned into an exit code.
SERVICES: dict[str, str] = {s: f"run_{s}" for s in
                            ("supervisor", "engine", "bridge", "api", "webhook", "scheduler", "telegram", "news")}


def _returns_3(calls: list[Any]) -> Callable[..., int]:
    """A stand-in entry function: records its call and returns 3 (no real command returns it)."""
    def fake(*args: Any, **kwargs: Any) -> int:
        calls.append(args)
        return 3
    return fake


def test_every_command_is_classed_as_one_shot_or_service():
    known = {argv[0] for argv in DISPATCH.values() if argv and not argv[0].startswith("-")} - {"no-such-command"}
    assert set(ONE_SHOT) | set(SERVICES) == known and not set(ONE_SHOT) & set(SERVICES)


@pytest.mark.parametrize("cmd", sorted(ONE_SHOT))
def test_a_one_shot_command_exits_with_its_entry_function_s_return_value(monkeypatch, cmd):
    import importlib
    module, attr = ONE_SHOT[cmd]
    calls: list[Any] = []
    monkeypatch.setattr(importlib.import_module(module), attr, _returns_3(calls))
    got = _dispatch(DISPATCH[cmd], stop=False)
    assert got == {"exit": 3, "printed_usage": False} and len(calls) == 1, got


@pytest.mark.parametrize("cmd", sorted(SERVICES))
def test_a_service_s_return_value_is_not_the_exit_code(monkeypatch, cmd):
    import goldbot.ops.run as run_mod
    calls: list[Any] = []
    monkeypatch.setattr(run_mod, SERVICES[cmd], _returns_3(calls))
    got = _dispatch(DISPATCH[cmd], stop=False)
    assert got == {"returned": True} and len(calls) == 1, got


def _entry_points() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for pattern in ENTRY_FILES:
        for f in sorted(ROOT.glob(pattern)):
            if not f.is_file() or f.suffix in (".pyc", ".md"):
                continue
            try:
                text = f.read_text()
            except UnicodeDecodeError:
                continue
            calls = [" ".join(m.group(1).split()) for m in INVOKE.finditer(text)]
            if calls:
                found[f.relative_to(ROOT).as_posix()] = calls
    return found


def test_the_service_and_deploy_entry_points_are_unchanged():
    _check("entry_points", _entry_points())


def test_every_entry_point_command_is_one_the_dispatcher_knows():
    known = {argv[0] for argv in DISPATCH.values() if argv and not argv[0].startswith("-")} - {"no-such-command"}
    used = {call.split()[0] for calls in _entry_points().values() for call in calls if call.split()}
    assert used and used <= known, used - known


def test_importing_the_run_module_loads_no_third_party_package():
    code = ("import sys; before = set(sys.modules); import goldbot.ops.run; "
            "new = {m.split('.')[0] for m in set(sys.modules) - before}; "
            "print(sorted(m for m in new if m not in sys.stdlib_module_names and not m.startswith('_')))")
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, PYTHONPATH=str(ROOT)))
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "['goldbot']"
