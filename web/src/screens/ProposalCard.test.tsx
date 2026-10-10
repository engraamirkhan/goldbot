import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { DecidedProposal, Proposal } from "../lib/api";
import { ProposalCard } from "./ProposalCard";

const NOW = Date.parse("2026-10-10T12:00:00Z");
const p = (expiresIn = 60, over: Partial<Proposal> = {}): Proposal => ({
  proposal_id: "p1", account_id: "icm-demo", agent_id: "session_open-g0-abc", side: "long", lots: 0.05, entry: 2400.25,
  stop: 2396.0, target: 2406.5, p: 0.62, ev_r: 0.31, spread_points: 22, risk_usd: 21.25,
  top_features: [["adx14", 31.2], ["atr_14", 0.4], ["rv_20", -0.2], ["extra", 1]],
  expires_at: new Date(NOW + expiresIn * 1000).toISOString(), ...over,
});
const decided = (status: DecidedProposal["status"], over: Partial<DecidedProposal> = {}): DecidedProposal => ({ ...p(), status, refusal: [], ...over });

describe("ProposalCard", () => {
  it("shows direction, size, $ risk, levels, p/EV/spread and top features in plain words", () => {
    render(<ProposalCard p={p()} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByText("LONG")).toBeInTheDocument();
    expect(screen.getByText(/0.05 lots/)).toBeInTheDocument();
    expect(screen.getByText("$21.25")).toBeInTheDocument();
    expect(screen.getByText("2400.25")).toBeInTheDocument();
    expect(screen.getByText("2396.00")).toBeInTheDocument();
    expect(screen.getByText("−4.25")).toBeInTheDocument();          // stop distance in price
    expect(screen.getByText("2406.50")).toBeInTheDocument();
    expect(screen.getByText("p 0.62")).toBeInTheDocument();
    expect(screen.getByText("+0.31 R")).toBeInTheDocument();
    expect(screen.getByText("22 pt")).toBeInTheDocument();
    expect(screen.getByText(/Trend strength \(ADX 14\)/)).toBeInTheDocument();
    expect(screen.queryByText(/Extra/)).toBeNull();                  // top three only
    expect(screen.getByRole("timer")).toHaveTextContent("1:00");
  });

  it("says when the engine did not report $ risk", () => {
    render(<ProposalCard p={p(60, { risk_usd: null })} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByTitle(/does not report \$ risk/)).toHaveTextContent("—");
  });

  it("approves with one tap", async () => {
    const onApprove = vi.fn();
    render(<ProposalCard p={p()} now={NOW} canDecide blockers={[]} onApprove={onApprove} />);
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onApprove).toHaveBeenCalledOnce();
  });

  it("asks for a reason code before rejecting, and can be cancelled", async () => {
    const onReject = vi.fn();
    render(<ProposalCard p={p()} now={NOW} canDecide blockers={[]} onReject={onReject} />);
    await userEvent.click(screen.getByRole("button", { name: "Reject…" }));
    const group = screen.getByRole("group", { name: "Reason for rejecting" });
    expect([...group.querySelectorAll("button")].map((b) => b.textContent)).toEqual(
      ["News", "Cost", "Discretion", "Duplicate", "Other", "Cancel"]);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onReject).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Reject…" }));
    await userEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    expect(onReject).toHaveBeenCalledWith("duplicate");
  });

  it("turns urgent in the last 15 s", () => {
    const { container } = render(<ProposalCard p={p(12)} now={NOW} canDecide blockers={[]} />);
    expect(container.querySelector("article")).toHaveClass("expiring");
    expect(screen.getByRole("timer")).toHaveTextContent("0:12");
    expect(screen.getByText("15 seconds left to decide")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("removes the buttons once expired", () => {
    render(<ProposalCard p={p(-1)} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent("Expired. Nobody approved within 90 s; no order.");
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("timer")).toBeNull();
  });

  it.each([
    ["submitted", {}, /Approval sent\. The engine re-checks risk/],
    ["approved", { decided_by: "telegram:111" }, /Approved, order sent\..*via Telegram/],
    ["rejected", { reason_code: "cost" as const }, /Rejected · Cost/],
    ["refused", { refusal: ["owner_halt", "drift_halt"] }, /Refused at approval\. The RiskGate blocked the order \(owner halt, drift halt\); nothing was sent/],
    ["expired", {}, /Expired\./],
  ])("shows the %s outcome without actions", (status, over, copy) => {
    render(<ProposalCard p={decided(status as DecidedProposal["status"], over)} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent(copy);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("warns that the RiskGate will refuse while entries are blocked", () => {
    render(<ProposalCard p={p()} now={NOW} canDecide blockers={["owner halt"]} />);
    expect(screen.getByText(/New entries are blocked \(owner halt\)/)).toBeInTheDocument();
  });

  it("shows sending and errors on the card", () => {
    render(<ProposalCard p={p()} now={NOW} canDecide blockers={[]} busy error="already decided" />);
    expect(screen.getByRole("button", { name: "Sending…" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("already decided");
  });

  it("does not offer decisions to a viewer", () => {
    render(<ProposalCard p={p()} now={NOW} canDecide={false} blockers={[]} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/Viewer role/)).toBeInTheDocument();
  });
});
