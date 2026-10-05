"""Tools the staff agents may call (design: "Agents at the app's disposal").

None can place, modify or close a trade, change a model, or edit configuration. The writes are research
bookkeeping: `file_hypothesis` appends a proposal to state/hypotheses.jsonl; the research analyst's `run_trial` runs a
bounded walk-forward for a hypothesis (recorded in the trial registry, so it raises the deflated-Sharpe bar) and
`update_hypothesis` records the verdict. A hypothesis changes nothing until it has passed the walk-forward,
deflated-Sharpe and shadow gates and been promoted by the population rules.
Results are JSON text, capped at MAX_RESULT_CHARS so a large table cannot flood the context.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from goldbot.data.store import Store

MAX_RESULT_CHARS = 20_000
MAX_TRIALS_PER_RUN = 2
MAX_OVERRIDE_CHANGE = 0.5          # a trial may move a numeric setting at most +-50% from its default
HYPOTHESIS_STATUSES = ["tested_promising", "tested_rejected", "inconclusive"]

# (family, overrides, rationale) -> trial summary; injected so tests and the sandbox need no bars or models
TrialRunner = Callable[[str, dict[str, Any], str], dict[str, Any]]


def hypothesis_id(row: dict[str, Any]) -> str:
    return row.get("id") or hashlib.sha1(f"{row.get('ts')}|{row.get('title')}".encode()).hexdigest()[:8]


def validate_overrides(family: str, pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Overrides for a trial: existing numeric settings of the family only, within +-MAX_OVERRIDE_CHANGE of default."""
    from goldbot.specialists import SPECIALISTS
    if family not in SPECIALISTS:
        raise ValueError(f"unknown family {family!r}; known: {sorted(SPECIALISTS)}")
    base = SPECIALISTS[family].default_config
    out: dict[str, Any] = {}
    for pair in pairs:
        key, value = str(pair["key"]), pair["value"]
        if key not in base or isinstance(base[key], bool) or not isinstance(base[key], (int, float)):
            raise ValueError(f"{key!r} is not a numeric setting of {family}; numeric settings: "
                             f"{sorted(k for k, v in base.items() if isinstance(v, (int, float)) and not isinstance(v, bool))}")
        lo, hi = sorted((base[key] * (1 - MAX_OVERRIDE_CHANGE), base[key] * (1 + MAX_OVERRIDE_CHANGE)))
        if not lo <= float(value) <= hi:
            raise ValueError(f"{key}={value} is outside +-{MAX_OVERRIDE_CHANGE:.0%} of its default {base[key]}")
        out[key] = int(round(value)) if isinstance(base[key], int) else float(value)
    if not out:
        raise ValueError("a trial needs at least one override")
    if all(out[k] == base[k] for k in out):
        raise ValueError("overrides equal the defaults: that trial is the champion's config")
    return out


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

    def __init__(self, state_dir: str | Path, store: Store, now: Callable[[], pd.Timestamp] | None = None,
                 trial_runner: TrialRunner | None = None):
        self.state = Path(state_dir)
        self.store = store
        self.now = now or (lambda: pd.Timestamp.now("UTC"))
        self.trial_runner = trial_runner
        self._trials_this_run = 0
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
            "run_trial": ("Run one bounded walk-forward trial of a hypothesis: the family's default config with the given "
                          f"numeric overrides (each within +-{MAX_OVERRIDE_CHANGE:.0%} of its default; at most "
                          f"{MAX_TRIALS_PER_RUN} trials per run). Takes minutes. Every trial is recorded in the registry "
                          "and raises the deflated-Sharpe bar for all future trials, so run only what the evidence supports.",
                          _schema({"hypothesis_id": {"type": "string"}, "family": {"type": "string"},
                                   "overrides": {"type": "array", "items": _schema({"key": {"type": "string"}, "value": {"type": "number"}},
                                                                                     ["key", "value"])},
                                   "rationale": {"type": "string", "description": "why these values test the hypothesis"}},
                                  ["hypothesis_id", "family", "overrides", "rationale"]), self.run_trial),
            "update_hypothesis": ("Record the verdict on a hypothesis after testing it.",
                                  _schema({"hypothesis_id": {"type": "string"},
                                           "status": {"type": "string", "enum": HYPOTHESIS_STATUSES},
                                           "summary": {"type": "string", "description": "the numbers behind the verdict, incl. trial ids"}},
                                          ["hypothesis_id", "status", "summary"]), self.update_hypothesis),
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

    def begin_run(self) -> None:
        """Called by the runner at the start of each agent run (per-run limits reset)."""
        self._trials_this_run = 0

    def read_hypotheses(self) -> list[dict[str, Any]]:
        f = self.state / "hypotheses.jsonl"
        rows = [json.loads(line) for line in f.read_text().splitlines() if line.strip()] if f.exists() else []
        return [{**r, "id": hypothesis_id(r)} for r in rows]

    def file_hypothesis(self, title: str, family: str, rationale: str, proposed_change: str, evidence: str) -> dict[str, Any]:
        row = {"ts": self.now().isoformat(), "title": title, "family": family, "rationale": rationale,
               "proposed_change": proposed_change, "evidence": evidence, "status": "proposed"}
        row["id"] = hypothesis_id(row)
        with open(self.state / "hypotheses.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return {"filed": title, "id": row["id"]}

    def _hypothesis(self, hid: str) -> dict[str, Any]:
        for r in self.read_hypotheses():
            if r["id"] == hid:
                return r
        raise ValueError(f"no hypothesis with id {hid!r}")

    def run_trial(self, hypothesis_id: str, family: str, overrides: list[dict[str, Any]], rationale: str) -> dict[str, Any]:
        if self.trial_runner is None:
            raise RuntimeError("trials can only run on the research host (no trial runner configured)")
        if self._trials_this_run >= MAX_TRIALS_PER_RUN:
            raise RuntimeError(f"trial limit reached for this run ({MAX_TRIALS_PER_RUN})")
        h = self._hypothesis(hypothesis_id)
        ov = validate_overrides(family, overrides)
        self._trials_this_run += 1
        result = self.trial_runner(family, ov, f"research analyst, hypothesis {hypothesis_id} ({h['title']}): {rationale}")
        return {"hypothesis_id": hypothesis_id, "overrides": ov, **result}

    def update_hypothesis(self, hypothesis_id: str, status: str, summary: str) -> dict[str, Any]:
        if status not in HYPOTHESIS_STATUSES:
            raise ValueError(f"status must be one of {HYPOTHESIS_STATUSES}")
        rows = self.read_hypotheses()
        hit = [r for r in rows if r["id"] == hypothesis_id]
        if not hit:
            raise ValueError(f"no hypothesis with id {hypothesis_id!r}")
        hit[0].update(status=status, verdict=summary, decided_utc=self.now().isoformat())
        f = self.state / "hypotheses.jsonl"
        tmp = f.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
        tmp.replace(f)
        return {"updated": hypothesis_id, "status": status}
