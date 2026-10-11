"""Runs one staff agent: a tool-use loop over ReadOnlyTools with a per-run and a monthly spending cap.

The loop is written out (rather than the SDK's beta tool runner) because the spending cap must be enforced between
turns and the whole run must be testable without the network. Every run is logged to aux.db table agent_runs (mirrored
to state/agent_runs.jsonl for the dashboard) with its inputs, tool calls, usage and cost, and its report is written
to state/agent_reports/<role>/<UTC time>.md.

Model and request shape follow the Claude API guidance for this SDK (anthropic 1.x): Claude Opus 5.5 with thinking on
(it cannot be disabled; depth is set by `effort`), automatic prompt caching of the stable system prompt and tools, and
server-side refusal fallbacks (`fallbacks="default"`), so a declined request is retried on a suitable model instead of
failing the job. A run that still ends in a refusal is logged with its category, not raised.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal, Protocol

import pandas as pd
from pydantic import Field

from goldbot.agents.roles import Role
from goldbot.agents.tools import ReadOnlyTools
from goldbot.base import Record, UtcTimestamp
from goldbot.db import Database
from goldbot.db.bookkeeping import (
    AGENT_SPEND,
    AgentRunLog,
    add_spend,
    all_spend,
    dual_read,
    export_spend,
    merge_agent_spend_json,
    month_spent,
    render_spend,
    reserve_spend,
)

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16_000
# USD per million tokens for MODEL (input, output, cache read, cache write at 1.25x input for the 5-minute TTL)
PRICE_PER_MTOK = {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00}

RunStatus = Literal["ok", "budget_exhausted", "monthly_cap", "refused", "max_turns", "error"]


class Client(Protocol):
    """The slice of anthropic.Anthropic the runner uses (lets tests pass a fake)."""
    @property
    def beta(self) -> Any: ...


class AgentRun(Record):
    role: str
    started_utc: UtcTimestamp
    finished_utc: UtcTimestamp | None = None
    status: RunStatus = "ok"
    turns: int = 0
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    cost_usd: float = 0.0
    report: str = ""
    report_path: str | None = None
    detail: str | None = None


def cost_of(usage: Any) -> tuple[dict[str, int], float]:
    u = {"input": int(getattr(usage, "input_tokens", 0) or 0), "output": int(getattr(usage, "output_tokens", 0) or 0),
         "cache_read": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
         "cache_write": int(getattr(usage, "cache_creation_input_tokens", 0) or 0)}
    return u, sum(u[k] * PRICE_PER_MTOK[k] for k in u) / 1e6


class SpendLedger:
    """Month -> USD spent by all agents (design: their total monthly spend is capped), in aux.db table agent_spend
    (state store step 3; state/agent_spend.json is imported once, then regenerated after every commit for
    ops/health.py and a rolled-back release, whose own writes to it are merged back by the larger amount per month).
    `path` names that file; the database is aux.db in the same folder. A failed export never undoes a booking.

    Agents reserve their per-run budget up front (`reserve`) and settle the real cost afterwards (`settle`): the
    cap check and the booking commit together under BEGIN IMMEDIATE, so concurrent runs (and the headline scorer,
    which shares this ledger) are never granted more than the cap between them. A run that dies mid-way (an
    exception out of the loop, or a killed process) keeps its reservation booked: unknown spend counts as spent
    (fails closed on money)."""

    def __init__(self, path: str | Path, monthly_cap_usd: float):
        self.path = Path(path)
        self.dir = self.path.parent
        self.cap = monthly_cap_usd
        self.db = Database.open(self.dir, "aux")
        if dual_read(self.db, AGENT_SPEND, self.dir, merge_agent_spend_json, render_spend):
            export_spend(self.db, self.dir)

    @property
    def spent(self) -> dict[str, float]:
        with self.db.connection() as conn:
            return all_spend(conn)

    def month_spent(self, now: pd.Timestamp) -> float:
        with self.db.connection() as conn:
            return month_spent(conn, f"{now:%Y-%m}")

    def remaining(self, now: pd.Timestamp) -> float:
        return max(self.cap - self.month_spent(now), 0.0)

    def add(self, now: pd.Timestamp, usd: float) -> None:
        """Book spend that has already happened (no cap check: the money is gone)."""
        with self.db.write() as conn:
            add_spend(conn, f"{now:%Y-%m}", usd)
        export_spend(self.db, self.dir)

    def reserve(self, now: pd.Timestamp, usd: float) -> float:
        """Grant and book min(usd, remaining) atomically; returns the grant (0 when the cap is used up)."""
        with self.db.write() as conn:
            granted = reserve_spend(conn, f"{now:%Y-%m}", usd, self.cap)
        export_spend(self.db, self.dir)
        return granted

    def settle(self, now: pd.Timestamp, reserved: float, actual: float) -> None:
        """Replace a reservation by the real cost (releases what was not used; books an overrun of the last turn)."""
        if reserved != actual:
            self.add(now, actual - reserved)


class AgentRunner:
    def __init__(self, client: Client, tools: ReadOnlyTools, ledger: SpendLedger, state_dir: str | Path,
                 model: str = MODEL):
        self.client, self.tools, self.ledger, self.model = client, tools, ledger, model
        self.state = Path(state_dir)

    def run(self, role: Role, now: pd.Timestamp, extra_context: str = "") -> AgentRun:
        run = AgentRun(role=role.name, started_utc=now)
        budget = self.ledger.reserve(now, role.max_cost_usd)
        if budget < 0.05:
            self._settle(now, budget, 0.0)
            run.status, run.detail = "monthly_cap", f"monthly agent budget {self.ledger.cap:.2f} USD used up"
            return self._finish(run, now)
        try:
            self._loop(role, now, extra_context, run, budget)
        except BaseException:
            # the loop catches API errors itself; anything escaping (interrupt, bug) leaves the cost unknown, so the
            # whole reservation stays booked (fails closed on money)
            log.warning("agent run %s died mid-loop: its %.2f USD reservation stays booked", role.name, budget)
            raise
        self._settle(now, budget, run.cost_usd)
        return self._finish(run, now)

    def _settle(self, now: pd.Timestamp, reserved: float, actual: float) -> None:
        """A failed settle (aux.db busy or broken) leaves the reservation booked, which errs towards stopping; the
        run is still logged and its report kept."""
        try:
            self.ledger.settle(now, reserved, actual)
        except Exception as exc:                          # noqa: BLE001
            log.warning("could not settle the agent spend (reservation %.2f, cost %.2f USD stays reserved): %s",
                        reserved, actual, exc)

    def _loop(self, role: Role, now: pd.Timestamp, extra_context: str, run: AgentRun, budget: float) -> None:
        self.tools.begin_run()
        tools = self.tools.definitions(list(role.tools))
        prompt = f"Current UTC time: {now.isoformat()}.\n\n{role.task}" + (f"\n\nContext:\n{extra_context}" if extra_context else "")
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        try:
            while True:
                if run.turns >= role.max_turns:
                    run.status = "max_turns"
                    break
                resp = self.client.beta.messages.create(
                    model=self.model, max_tokens=MAX_TOKENS, system=role.system, tools=tools, messages=messages,
                    output_config={"effort": role.effort}, cache_control={"type": "ephemeral"},
                    betas=[FALLBACK_BETA], fallbacks="default")
                run.turns += 1
                u, usd = cost_of(resp.usage)
                for k, v in u.items():
                    run.usage[k] = run.usage.get(k, 0) + v
                run.cost_usd += usd
                if resp.stop_reason == "refusal":
                    det = getattr(resp, "stop_details", None)
                    run.status, run.detail = "refused", f"refusal: {getattr(det, 'category', None)}"
                    break
                text = "".join(b.text for b in resp.content if b.type == "text")
                if resp.stop_reason != "tool_use":
                    run.report = text
                    if resp.stop_reason == "max_tokens":
                        run.detail = "report truncated at max_tokens"
                    break
                if run.cost_usd >= budget:
                    # stop before spending more: keep what was written so far as the report
                    run.status, run.report = "budget_exhausted", text or "(stopped by the per-run budget before a report was written)"
                    break
                messages.append({"role": "assistant", "content": resp.content})   # full content: thinking blocks stay valid
                results = []
                for b in resp.content:
                    if b.type != "tool_use":
                        continue
                    out, is_err = self.tools.call(b.name, dict(b.input), list(role.tools))
                    run.tool_calls.append({"name": b.name, "input": dict(b.input), "error": is_err, "chars": len(out)})
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": out, "is_error": is_err})
                messages.append({"role": "user", "content": results})   # all results in one message
        except Exception as exc:     # API or network trouble: logged, the scheduler's next slot tries again
            run.status, run.detail = "error", f"{type(exc).__name__}: {exc}"

    def _finish(self, run: AgentRun, now: pd.Timestamp) -> AgentRun:
        run.finished_utc = now if run.finished_utc is None else run.finished_utc
        if run.report:
            d = self.state / "agent_reports" / run.role
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{now:%Y%m%dT%H%M%S}.md"
            path.write_text(f"<!-- {run.role} {now.isoformat()} status={run.status} cost=${run.cost_usd:.3f} -->\n{run.report}\n")
            run.report_path = str(path)
        AgentRunLog(self.state).append(run.model_dump_json(exclude={"report"}))
        return run
