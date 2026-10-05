"""Runs one staff agent: a tool-use loop over ReadOnlyTools with a per-run and a monthly spending cap.

The loop is written out (rather than the SDK's beta tool runner) because the spending cap must be enforced between
turns and the whole run must be testable without the network. Every run is logged to state/agent_runs.jsonl with its
inputs, tool calls, usage and cost, and its report is written to state/agent_reports/<role>/<UTC time>.md.

Model and request shape follow the Claude API guidance for this SDK (anthropic 1.x): Claude Opus 5.5 with thinking on
(it cannot be disabled; depth is set by `effort`), automatic prompt caching of the stable system prompt and tools, and
server-side refusal fallbacks (`fallbacks="default"`), so a declined request is retried on a suitable model instead of
failing the job. A run that still ends in a refusal is logged with its category, not raised.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Protocol

import pandas as pd
from pydantic import Field

from goldbot.agents.roles import Role
from goldbot.agents.tools import ReadOnlyTools
from goldbot.base import Record, UtcTimestamp

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
    """Month -> USD spent by all agents (design: their total monthly spend is capped)."""

    def __init__(self, path: str | Path, monthly_cap_usd: float):
        self.path = Path(path)
        self.cap = monthly_cap_usd
        self.spent: dict[str, float] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def month_spent(self, now: pd.Timestamp) -> float:
        return float(self.spent.get(f"{now:%Y-%m}", 0.0))

    def remaining(self, now: pd.Timestamp) -> float:
        return max(self.cap - self.month_spent(now), 0.0)

    def add(self, now: pd.Timestamp, usd: float) -> None:
        key = f"{now:%Y-%m}"
        self.spent[key] = round(self.spent.get(key, 0.0) + usd, 6)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.spent, indent=1))


class AgentRunner:
    def __init__(self, client: Client, tools: ReadOnlyTools, ledger: SpendLedger, state_dir: str | Path,
                 model: str = MODEL):
        self.client, self.tools, self.ledger, self.model = client, tools, ledger, model
        self.state = Path(state_dir)

    def run(self, role: Role, now: pd.Timestamp, extra_context: str = "") -> AgentRun:
        run = AgentRun(role=role.name, started_utc=now)
        budget = min(role.max_cost_usd, self.ledger.remaining(now))
        if budget < 0.05:
            run.status, run.detail = "monthly_cap", f"monthly agent budget {self.ledger.cap:.2f} USD used up"
            return self._finish(run, now)
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
        self.ledger.add(now, run.cost_usd)
        return self._finish(run, now)

    def _finish(self, run: AgentRun, now: pd.Timestamp) -> AgentRun:
        run.finished_utc = now if run.finished_utc is None else run.finished_utc
        if run.report:
            d = self.state / "agent_reports" / run.role
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{now:%Y%m%dT%H%M%S}.md"
            path.write_text(f"<!-- {run.role} {now.isoformat()} status={run.status} cost=${run.cost_usd:.3f} -->\n{run.report}\n")
            run.report_path = str(path)
        self.state.mkdir(parents=True, exist_ok=True)
        with open(self.state / "agent_runs.jsonl", "a", encoding="utf-8") as fh:
            fh.write(run.model_dump_json(exclude={"report"}) + "\n")
        return run
