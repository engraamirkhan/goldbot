"""Tools the staff agents may call (design: "Agents at the app's disposal").

Every tool reads; none can place, modify or close a trade, change a model, or edit configuration. The one write is
`file_hypothesis`, which appends a proposal to state/hypotheses.jsonl for the research analyst and the owner: a
hypothesis changes nothing until it has passed the walk-forward, deflated-Sharpe and shadow gates.
Results are JSON text, capped at MAX_RESULT_CHARS so a large table cannot flood the context.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from goldbot.data.store import Store

MAX_RESULT_CHARS = 20_000


def _clip(obj: Any) -> str:
    text = json.dumps(obj, default=str)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + f'... [truncated {len(text) - MAX_RESULT_CHARS} chars; narrow the query]'


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [{str(k): v for k, v in r.items()} for r in df.tail(500).to_dict("records")]


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


class ReadOnlyTools:
    """The registry of tools exposed to agents; each role is given a subset by name."""

    def __init__(self, state_dir: str | Path, store: Store, now: Callable[[], pd.Timestamp] | None = None):
        self.state = Path(state_dir)
        self.store = store
        self.now = now or (lambda: pd.Timestamp.now("UTC"))
        self._defs: dict[str, tuple[str, dict[str, Any], Callable[..., Any]]] = {
            "read_decisions": ("Trade journal: the engines' decisions (proposals, gate blocks, orders, exits) for the last N days.",
                               _schema({"days": {"type": "integer", "description": "1-90"},
                                        "account_id": {"type": "string", "description": "account id, or empty for all accounts"}},
                                       ["days", "account_id"]),
                               self.read_decisions),
            "read_fills": ("Executed fills with requested vs filled price for one account over the last N days.",
                           _schema({"account_id": {"type": "string"}, "days": {"type": "integer", "description": "1-365"}},
                                   ["account_id", "days"]), self.read_fills),
            "read_dq_events": ("Data-quality events (gaps, spikes, crossed quotes) recorded by the loaders over the last N days.",
                               _schema({"days": {"type": "integer", "description": "1-90"}}, ["days"]), self.read_dq_events),
            "read_state": ("A state file the services write. name is one of: supervisor, scheduler, population, agents, "
                           "costs_<account>, classifier_<account>, engine_<account>.",
                           _schema({"name": {"type": "string"}}, ["name"]), self.read_state),
            "read_shadow_stats": ("Shadow-book performance (n_trades, Sharpe, hit rate, drawdown, turnover) per model version.",
                                  _schema({}), self.read_shadow_stats),
            "read_research_registry": ("The last N trials in the research registry (config, rationale, results).",
                                       _schema({"last_n": {"type": "integer", "description": "1-100"}}, ["last_n"]),
                                       self.read_research_registry),
            "read_hypotheses": ("Hypotheses already filed (to avoid duplicates).", _schema({}), self.read_hypotheses),
            "file_hypothesis": ("File a hypothesis for the research analyst to test. It changes nothing by itself.",
                                _schema({"title": {"type": "string"}, "family": {"type": "string"},
                                         "rationale": {"type": "string", "description": "one paragraph: what, why, expected effect"},
                                         "proposed_change": {"type": "string", "description": "feature / barrier / rule change to evaluate"},
                                         "evidence": {"type": "string", "description": "the numbers that motivated it"}},
                                        ["title", "family", "rationale", "proposed_change", "evidence"]),
                                self.file_hypothesis),
        }

    # ------------------------------------------------------------------ API-facing definitions
    def definitions(self, names: list[str]) -> list[dict[str, Any]]:
        out = []
        for n in names:
            desc, schema, _ = self._defs[n]
            out.append({"name": n, "description": desc, "input_schema": schema, "strict": True})
        return out

    def call(self, name: str, args: dict[str, Any], allowed: list[str]) -> tuple[str, bool]:
        """(result text, is_error). A tool outside the role's list is refused, never executed."""
        if name not in allowed or name not in self._defs:
            return f"tool {name!r} is not available to this agent", True
        try:
            return _clip(self._defs[name][2](**args)), False
        except Exception as exc:   # a bad argument becomes an error result the model can correct
            return f"{type(exc).__name__}: {exc}", True

    # ------------------------------------------------------------------ tools
    def _since(self, days: int, cap: int = 90) -> pd.Timestamp:
        # strict tool schemas cannot carry numeric bounds, so ranges are enforced here
        return self.now() - pd.Timedelta(days=min(max(int(days), 1), cap))

    def read_decisions(self, days: int, account_id: str = "") -> list[dict[str, Any]]:
        df = self.store.read("decisions", source=account_id or None, start=self._since(days))
        cols = [c for c in ("ts_utc", "account_id", "agent_id", "action", "p", "mult", "proposal_id", "detail") if c in df]
        return _records(df[cols]) if not df.empty else []

    def read_fills(self, account_id: str, days: int) -> list[dict[str, Any]]:
        df = self.store.read("fills", source=account_id, start=self._since(days, cap=365))
        return _records(df.drop(columns=["source", "symbol", "year", "month"], errors="ignore")) if not df.empty else []

    def read_dq_events(self, days: int) -> list[dict[str, Any]]:
        df = self.store.read("dq_events", start=self._since(days))
        return _records(df) if not df.empty else []

    def read_state(self, name: str) -> Any:
        if not name.replace("_", "").replace("-", "").isalnum():
            raise ValueError("bad state name")
        f = self.state / f"{name}.json"
        if not f.exists():
            return {"missing": name}
        return json.loads(f.read_text())

    def read_shadow_stats(self) -> dict[str, Any]:
        return {f.stem.removeprefix("shadow_"): json.loads(f.read_text())
                for f in sorted(self.state.glob("shadow_*.json")) if f.name != "shadow_book.json"}

    def read_research_registry(self, last_n: int) -> list[dict[str, Any]]:
        f = self.state / "research_registry.jsonl"
        rows = [json.loads(line) for line in f.read_text().splitlines() if line.strip()] if f.exists() else []
        return rows[-min(max(int(last_n), 1), 100):]

    def read_hypotheses(self) -> list[dict[str, Any]]:
        f = self.state / "hypotheses.jsonl"
        return [json.loads(line) for line in f.read_text().splitlines() if line.strip()] if f.exists() else []

    def file_hypothesis(self, title: str, family: str, rationale: str, proposed_change: str, evidence: str) -> dict[str, Any]:
        row = {"ts": self.now().isoformat(), "title": title, "family": family, "rationale": rationale,
               "proposed_change": proposed_change, "evidence": evidence, "status": "proposed"}
        with open(self.state / "hypotheses.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return {"filed": title}
