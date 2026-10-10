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
      halted: true, halted_by: "dashboard:o@x.io", halt_reason: "fomc", drift_halt: true, drift_reasons: ["ECE 0.12"],
      blackout: { kind: "calendar", title: "US CPI", ts_utc: "2026-10-10T12:30:00Z", received_utc: null, accounts: ["icm-demo"] },
    })} />);
    expect(screen.getByRole("status")).toHaveTextContent("New entries blocked: owner halt, drift halt, news blackout, drawdown halt");
    expect(chip("Owner halt")).toHaveClass("bad");
    expect(chip("Owner halt")).toHaveTextContent("dashboard:o@x.io: fomc");
    expect(chip("Drift halt")).toHaveTextContent("ECE 0.12");
    expect(chip("News blackout")).toHaveTextContent("US CPI");
    expect(chip("Drawdown")).toHaveClass("bad");
  });

  it("uses amber, not red, for size-down", () => {
    render(<SafetyStrip status={status()} accounts={[account({ stage: "size_down" })]} />);
    expect(chip("Drawdown")).toHaveClass("warn");
    expect(screen.getByRole("status")).toHaveTextContent("New entries allowed");
  });

  it("says when no engine is reporting", () => {
    render(<SafetyStrip status={status()} accounts={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent("No engines reporting");
    expect(chip("Drawdown")).toHaveTextContent("no engines");
  });

  it("shows loading and error states", () => {
    const { rerender } = render(<SafetyStrip />);
    expect(screen.getByRole("status")).toHaveTextContent("Checking safety state");
    rerender(<SafetyStrip error />);
    expect(screen.getByRole("alert")).toHaveTextContent("Safety state unavailable");
  });
});
