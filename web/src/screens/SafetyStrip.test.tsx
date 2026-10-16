import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AccountSummary, Status } from "../lib/api";
import { SafetyStrip } from "./SafetyStrip";

const status = (over: Partial<Status> = {}): Status => ({
  mode: "propose", halted: false, pending: 0, supervisor: {}, supervisor_halt: false, supervisor_reasons: [], drift_halt: false,
  drift_reasons: [], blackout: null, ...over,
});
const account = (over: Partial<AccountSummary> = {}): AccountSummary => ({
  account_id: "icm-demo", broker: "icm", mode: "demo", equity: 10000, day_pnl_pct: 0, week_pnl_pct: 0, drawdown_pct: 0.012,
  stage: "normal", open_positions: 0, account_class: "raw", ...over,
});
const chip = (label: string) => screen.getByText(label, { selector: ".k" }).closest("li");

describe("SafetyStrip", () => {
  it("says entries are allowed when nothing blocks them", () => {
    render(<SafetyStrip status={status()} accounts={[account()]} />);
    expect(screen.getByRole("status")).toHaveTextContent("New entries allowed");
    expect(chip("Owner halt")).toHaveTextContent("off");
    expect(chip("Drift halt")).toHaveTextContent("off");
    expect(chip("Drawdown")).toHaveTextContent("normal1.2%");
    expect(chip("News")).toHaveTextContent("clear");
    expect(chip("Account")).toHaveTextContent("raw · demo");
    expect(screen.getByRole("list").querySelectorAll(".chip.bad")).toHaveLength(0);
  });

  it("shows every halt in red with its reason", () => {
    render(<SafetyStrip accounts={[account({ stage: "halted" })]} status={status({
      halted: true, halted_by: "dashboard", halt_reason: "fomc", drift_halt: true, drift_reasons: ["ECE 0.12"],
      blackout: { kind: "calendar", title: "US CPI", ts_utc: "2026-10-10T12:30:00Z", received_utc: null, accounts: ["icm-demo"] },
    })} />);
    expect(screen.getByRole("status")).toHaveTextContent("New entries blocked: owner halt, drift halt, news blackout, drawdown halt");
    expect(chip("Owner halt")).toHaveClass("bad");
    expect(chip("Owner halt")).toHaveTextContent("via the dashboard: fomc");
    expect(chip("Drift halt")).toHaveTextContent("ECE 0.12");
    expect(chip("News blackout")).toHaveTextContent("US CPI");
    expect(chip("Drawdown")).toHaveClass("bad");
  });

  it("uses amber, not red, for size-down", () => {
    render(<SafetyStrip status={status()} accounts={[account({ stage: "size_down" })]} />);
    expect(chip("Drawdown")).toHaveClass("warn");
    expect(screen.getByRole("status")).toHaveTextContent("New entries allowed");
  });

  it("words the summary with the API's entry status, so it agrees with the Overview panel", () => {
    const entry_status = { state: "waiting" as const, headline: "No new trades for now: Market closed", notes: [], exits: "x", as_of: null,
      blocks: [{ code: "market_closed", text: "Market closed", fix: "Nothing to do.", kind: "wait" as const, scope: "account" as const,
                 agent_id: null, at: null, at_label: null, runbook: null, runbook_url: null }] };
    render(<SafetyStrip status={status()} accounts={[account({ entry_status })]} />);
    expect(screen.getByRole("status")).toHaveTextContent("New entries blocked: Market closed");
  });

  it("says when no engine is reporting", () => {
    render(<SafetyStrip status={status()} accounts={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent("No engines reporting");
    expect(chip("Drawdown")).toHaveTextContent("no engines");
  });

  it("shows today's loss budget left, amber under a quarter and red when spent", () => {
    const budget = (remaining: number) => ({ limit: 15, used: 15 - remaining, remaining, binding: "combined 1.5% daily cap", binding_label: "all accounts together (1.5% of today's start)", period: "today" as const, notes: [], caps: [] });
    const { rerender } = render(<SafetyStrip status={status()} accounts={[account({ currency: "GBP", loss_budget: budget(5) })]} />);
    expect(chip("Loss budget")).toHaveTextContent("£5.00 left");
    expect(chip("Loss budget")).toHaveTextContent("limit £15.00 today");
    expect(chip("Loss budget")).toHaveClass("ok");
    rerender(<SafetyStrip status={status()} accounts={[account({ currency: "GBP", loss_budget: budget(3) })]} />);
    expect(chip("Loss budget")).toHaveClass("warn");
    rerender(<SafetyStrip status={status()} accounts={[account({ currency: "GBP", loss_budget: budget(0) })]} />);
    expect(chip("Loss budget")).toHaveClass("bad");
    expect(screen.getByRole("status")).toHaveTextContent("New entries blocked: daily loss limit reached");
    rerender(<SafetyStrip status={status()} accounts={[account({ currency: "GBP",
      loss_budget: { ...budget(0), binding: "account 5.0% weekly cap", period: "this week" as const } })]} />);
    expect(screen.getByRole("status")).toHaveTextContent("New entries blocked: weekly loss limit reached");
    expect(chip("Loss budget")).toHaveTextContent("limit £15.00 this week");
  });

  it("shows the loss budget as not reported for an engine without it", () => {
    render(<SafetyStrip status={status()} accounts={[account()]} />);
    expect(chip("Loss budget")).toHaveTextContent("not reported");
    expect(chip("Loss budget")).toHaveClass("idle");
  });

  it("never says allowed from stale data: a warning with the report's time instead", () => {
    const t0 = new Date(2026, 9, 14, 14, 2).getTime();      // local 14:02
    const stale = "Safety state from 14:02, can't reach the server; check before approving";
    const { rerender } = render(<SafetyStrip status={status()} accounts={[account()]} updatedAt={t0} now={t0 + 20_000} />);
    expect(screen.getByRole("alert")).toHaveTextContent(stale);
    expect(screen.getByRole("alert")).toHaveClass("warn");
    expect(screen.queryByText("New entries allowed")).toBeNull();
    rerender(<SafetyStrip status={status()} accounts={[account()]} updatedAt={t0} now={t0 + 1_000} error />);
    expect(screen.getByRole("alert")).toHaveTextContent(stale);
    expect(screen.queryByText("New entries allowed")).toBeNull();
    rerender(<SafetyStrip status={status()} accounts={[account()]} updatedAt={t0} now={t0 + 1_000} />);
    expect(screen.getByRole("status")).toHaveTextContent("New entries allowed");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows loading and error states", () => {
    const { rerender } = render(<SafetyStrip />);
    expect(screen.getByRole("status")).toHaveTextContent("Checking safety state");
    rerender(<SafetyStrip error />);
    expect(screen.getByRole("alert")).toHaveTextContent("Safety state unavailable");
  });
});
