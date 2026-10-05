"""Staff agents with a fake Anthropic client: request shape, tool loop, permissions, spending caps, logging."""
import json
from types import SimpleNamespace as NS
from typing import Any

import pandas as pd
import pytest

from goldbot.agents.roles import ROLES
from goldbot.agents.runner import FALLBACK_BETA, MODEL, AgentRunner, SpendLedger, cost_of
from goldbot.agents.tools import ReadOnlyTools
from goldbot.data.store import Store

NOW = pd.Timestamp("2026-10-05 23:45", tz="UTC")


def _usage(inp=1000, out=500, cr=0, cw=0):
    return NS(input_tokens=inp, output_tokens=out, cache_read_input_tokens=cr, cache_creation_input_tokens=cw)


def _tool(name, args, i="t1"):
    return NS(type="tool_use", id=i, name=name, input=args)


def _text(t):
    return NS(type="text", text=t)


class FakeClient:
    """Plays back scripted responses and records every request."""

    def __init__(self, responses: list[Any]):
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _resp(content, stop="end_turn", usage=None, stop_details=None):
    return NS(content=content, stop_reason=stop, usage=usage or _usage(), stop_details=stop_details)


@pytest.fixture
def setup(tmp_path):
    (tmp_path / "scheduler.json").write_text(json.dumps({"ts": NOW.isoformat(), "jobs": {"nightly_costs": {"last_ok": True}}}))
    tools = ReadOnlyTools(tmp_path, Store(tmp_path / "data"), now=lambda: NOW)
    return tmp_path, tools, SpendLedger(tmp_path / "spend.json", monthly_cap_usd=40.0)


def test_tool_loop_request_shape_report_and_log(setup):
    tmp, tools, ledger = setup
    client = FakeClient([
        _resp([NS(type="thinking", thinking=""), _tool("read_state", {"name": "scheduler"}),
               _tool("read_dq_events", {"days": 2}, i="t2")], stop="tool_use"),
        _resp([_text("All quiet.\n\n## Jobs\nnightly_costs ok")], usage=_usage(2000, 300, cr=1500)),
    ])
    run = AgentRunner(client, tools, ledger, tmp).run(ROLES["data_steward"], NOW)
    assert run.status == "ok" and run.turns == 2 and run.report.startswith("All quiet")
    first = client.requests[0]
    assert first["model"] == MODEL == "claude-opus-5-5"
    assert first["betas"] == [FALLBACK_BETA] and first["fallbacks"] == "default"
    assert first["output_config"] == {"effort": "medium"} and first["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in first                       # Opus 5.5: thinking is always on; effort sets the depth
    assert {t["name"] for t in first["tools"]} == set(ROLES["data_steward"].tools)
    second = client.requests[1]["messages"]
    assert second[1]["role"] == "assistant" and len(second[1]["content"]) == 3     # full content echoed (thinking kept)
    results = second[2]["content"]
    assert second[2]["role"] == "user" and [r["tool_use_id"] for r in results] == ["t1", "t2"]   # one message
    assert json.loads(results[0]["content"])["jobs"]["nightly_costs"]["last_ok"] is True
    # cost, ledger, log line and report file
    assert run.cost_usd == pytest.approx(cost_of(_usage())[1] + cost_of(_usage(2000, 300, cr=1500))[1])
    assert ledger.month_spent(NOW) == pytest.approx(run.cost_usd)
    log = json.loads((tmp / "agent_runs.jsonl").read_text().splitlines()[-1])
    assert log["role"] == "data_steward" and [c["name"] for c in log["tool_calls"]] == ["read_state", "read_dq_events"]
    assert run.report_path and "All quiet" in open(run.report_path).read()


def test_tools_outside_the_role_are_refused_and_not_run(setup):
    tmp, tools, ledger = setup
    client = FakeClient([
        _resp([_tool("file_hypothesis", {"title": "x", "family": "session_open", "rationale": "r",
                                         "proposed_change": "c", "evidence": "e"})], stop="tool_use"),
        _resp([_text("done")]),
    ])
    run = AgentRunner(client, tools, ledger, tmp).run(ROLES["data_steward"], NOW)
    assert run.tool_calls[0]["error"] is True and not (tmp / "hypotheses.jsonl").exists()
    assert "not available" in client.requests[1]["messages"][2]["content"][0]["content"]


def test_improvement_agent_files_a_hypothesis(setup):
    tmp, tools, ledger = setup
    client = FakeClient([
        _resp([_tool("read_hypotheses", {}),
               _tool("file_hypothesis", {"title": "Asian-range filter too loose", "family": "session_open",
                                         "rationale": "r", "proposed_change": "asia_range_max_atr_d 0.8 -> 0.6",
                                         "evidence": "hit rate 0.38 when range > 0.6 ATR"}, i="t2")], stop="tool_use"),
        _resp([_text("Filed one hypothesis.")]),
    ])
    run = AgentRunner(client, tools, ledger, tmp).run(ROLES["improvement_agent"], NOW)
    rows = [json.loads(x) for x in (tmp / "hypotheses.jsonl").read_text().splitlines()]
    assert run.status == "ok" and rows[0]["status"] == "proposed" and rows[0]["family"] == "session_open"
    assert client.requests[0]["output_config"] == {"effort": "high"}


def test_per_run_budget_stops_before_spending_more(setup):
    tmp, tools, ledger = setup
    pricey = _usage(inp=200_000, out=20_000)                  # $0.80 + $0.40 > the $1.00 cap of a daily role
    client = FakeClient([_resp([_text("partial"), _tool("read_state", {"name": "supervisor"})], stop="tool_use", usage=pricey)])
    run = AgentRunner(client, tools, ledger, tmp).run(ROLES["risk_officer"], NOW)
    assert run.status == "budget_exhausted" and run.report == "partial" and len(client.requests) == 1


def test_monthly_cap_blocks_the_run_without_calling_the_api(setup):
    tmp, tools, _ = setup
    ledger = SpendLedger(tmp / "spend.json", monthly_cap_usd=5.0)
    ledger.add(NOW, 4.99)
    client = FakeClient([])
    run = AgentRunner(client, tools, ledger, tmp).run(ROLES["risk_officer"], NOW)
    assert run.status == "monthly_cap" and client.requests == []
    assert ledger.month_spent(NOW + pd.Timedelta(days=30)) == 0.0          # a new month starts fresh


def test_refusal_and_api_errors_are_logged_not_raised(setup):
    tmp, tools, ledger = setup
    refused = FakeClient([_resp([], stop="refusal", stop_details=NS(category="cyber", explanation="x"))])
    run = AgentRunner(refused, tools, ledger, tmp).run(ROLES["data_steward"], NOW)
    assert run.status == "refused" and "cyber" in (run.detail or "")
    broken = FakeClient([ConnectionError("network down")])
    run = AgentRunner(broken, tools, ledger, tmp).run(ROLES["data_steward"], NOW)
    assert run.status == "error" and "network down" in (run.detail or "")
    assert len((tmp / "agent_runs.jsonl").read_text().splitlines()) == 2


def test_tool_schemas_stay_inside_the_strict_subset(setup):
    _, tools, _ = setup
    names = sorted({t for r in ROLES.values() for t in r.tools})
    for d in tools.definitions(names):
        sch = d["input_schema"]
        assert d["strict"] is True and sch["additionalProperties"] is False
        assert set(sch["required"]) == set(sch["properties"])           # every property required
        for prop in sch["properties"].values():
            assert not {"minimum", "maximum", "minLength", "maxLength"} & set(prop)


def test_read_tools_over_the_journal(setup):
    tmp, tools, _ = setup
    store = tools.store
    store.append("decisions", pd.DataFrame({"ts_utc": [NOW - pd.Timedelta(hours=2)], "account_id": ["icm-demo"],
                                            "agent_id": ["a"], "action": ["gate:max_positions"], "p": [0.6], "mult": [0.5],
                                            "proposal_id": [None], "detail": ["{}"]}), source="icm-demo", dedupe=False)
    out, err = tools.call("read_decisions", {"days": 1, "account_id": ""}, ["read_decisions"])
    assert not err and json.loads(out)[0]["action"] == "gate:max_positions"
    out, err = tools.call("read_state", {"name": "../../etc/passwd"}, ["read_state"])
    assert err and "bad state name" in out


def test_agent_jobs_are_skipped_without_a_key(tmp_path):
    from goldbot.config import load_settings
    from goldbot.ops.jobs import JobContext, agents_daily
    from goldbot.research.model_registry import ModelRegistry
    from goldbot.research.population import Population
    from goldbot.research.registry import TrialRegistry
    ctx = JobContext(settings=load_settings(), store=Store(tmp_path / "d"), state_dir=tmp_path, models=ModelRegistry(tmp_path / "m"),
                     trials=TrialRegistry(tmp_path / "t.jsonl"), accounts=[], population=Population(tmp_path / "p.json"))
    assert "skipped" in agents_daily(ctx, NOW)
