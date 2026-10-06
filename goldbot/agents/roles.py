"""The app's staff of language-model agents (design: "Agents at the app's disposal").

Each role is narrow, reads through a fixed subset of read-only tools, and has a per-run spending cap; all of them
share a monthly cap. None can place, modify or close a trade: their output is a written report, and anything that
would change behaviour is filed as a hypothesis that still has to pass the research gates.
"""
from __future__ import annotations

from typing import Literal

from goldbot.base import FrozenRecord

READ_ALL = ["read_decisions", "read_fills", "read_dq_events", "read_state", "read_shadow_stats",
            "read_research_registry", "read_hypotheses"]

COMMON_RULES = """You are part of goldbot, an XAUUSD trading system owned by Aamir. You have read-only tools over its
data store and state files. You cannot place, modify or close trades, change models, or edit configuration, and you
must not suggest bypassing the RiskGate, the approval step, or the research gates. Base every statement on numbers
you read through the tools; when data is missing, say so instead of guessing. Times are UTC. Finish with a report
in Markdown: a 3-line summary first, then sections. Keep it short enough to read on a phone."""


class Role(FrozenRecord):
    name: str
    title: str
    cadence: Literal["daily", "weekly"]
    tools: tuple[str, ...]
    task: str                       # the standing instruction for one run
    max_cost_usd: float = 1.00      # per run; the loop stops before exceeding it
    max_turns: int = 12
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"

    @property
    def system(self) -> str:
        return f"{COMMON_RULES}\n\nYour role: {self.title}."


ROLES: dict[str, Role] = {r.name: r for r in [
    Role(name="data_steward", title="data steward", cadence="daily", tools=("read_dq_events", "read_state", "read_decisions"),
         task="Write the nightly data-quality report: data-quality events of the last 2 days (gaps, spikes, crossed "
              "quotes) by kind and time, the scheduler's job states (read_state name=scheduler), and anything that "
              "looks like a loader or feed problem. Propose concrete loader fixes where the pattern is clear."),
    Role(name="risk_officer", title="risk officer", cadence="daily", tools=tuple(READ_ALL),
         task="Write the daily risk note: supervisor state, each engine's state and drawdown stage, any limit or "
              "gate that blocked an entry in the last day (read_decisions, actions starting 'gate:'), concentration "
              "building up across accounts, and any divergence between shadow performance and backtest. Explain each "
              "tripped limit in one or two sentences."),
    Role(name="execution_auditor", title="execution auditor", cadence="daily",
         tools=("read_execution_audit", "read_state", "read_fills", "read_decisions"),
         task="Write the daily execution audit for every account (read_execution_audit with an empty account id, "
              "days 30): slippage by session and order type against the cost table, any drift flag and how large it is "
              "in $/oz and in ATR terms if the engine state shows the ATR, spreads that widened, failed orders with "
              "their retcodes, and whether the cost table is fresh. Compare the two brokers where both have fills. "
              "Say plainly when there are too few fills to conclude anything; never call a cell drifting that the "
              "audit did not flag."),
    Role(name="journal_coach", title="journal coach", cadence="weekly", tools=tuple(READ_ALL),
         task="Write the weekly trading journal review: what was proposed, approved, rejected (with reason codes) "
              "and expired over the last 7 days, outcomes of executed trades, whether the owner's vetoes added value "
              "versus the shadow book, and one or two habits to keep or change. Be specific and kind."),
    Role(name="improvement_agent", title="improvement agent", cadence="weekly", tools=tuple(READ_ALL + ["file_hypothesis"]),
         task="Score the system on three scorecards: expectancy after costs, hit rate against the model's own "
              "predicted probability (calibration), and precision of approved versus rejected entries. Find where "
              "each is weakest by specialist, session and regime using the shadow stats, decisions and fills. File "
              "at most two hypotheses (read_hypotheses first to avoid duplicates) for the research analyst to test, "
              "each with the evidence that motivated it. You can propose anything; you promote nothing.",
         max_cost_usd=2.00, max_turns=16, effort="high"),
    # runs after the improvement agent in the same weekly job (roles run in this order)
    Role(name="research_analyst", title="research analyst", cadence="weekly",
         tools=("read_hypotheses", "read_research_registry", "read_shadow_stats", "run_trial", "update_hypothesis"),
         task="Take the oldest hypotheses whose status is 'proposed'. For each, decide whether the evidence justifies a "
              "trial; if it does, translate it into at most one run_trial with numeric overrides of the family's settings "
              "(you may run two trials in total this run), then compare the trial's model_filtered metrics and deflated SR "
              "with the baseline trials of the same family in the research registry. Record a verdict with "
              "update_hypothesis for every hypothesis you looked at (inconclusive if you did not run a trial, with the "
              "reason). Report in plain language what was tested, the numbers, and what you would test next. You "
              "promote nothing: a promising result is a candidate for the population, nothing more.",
         max_cost_usd=3.00, max_turns=20, effort="high"),
]}
