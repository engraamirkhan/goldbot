import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { DecidedProposal, Proposal } from "../lib/api";
import { ProposalCard } from "./ProposalCard";

const NOW = Date.parse("2026-10-10T12:00:00Z");
const p = (expiresIn = 60, over: Partial<Proposal> = {}): Proposal => ({
  proposal_id: "p1", account_id: "icm-demo", agent_id: "session_open-g0-abc", side: "long", lots: 0.05, entry: 2400.25,
  stop: 2396.0, target: 2406.5, p: 0.62, ev_r: 0.31, spread_points: 22, risk_usd: 21.25, min_lot_exception: false,
  top_features: [["adx14", 31.2], ["atr_14", 0.4], ["rv_20", -0.2], ["extra", 1]],
  expires_at: new Date(NOW + expiresIn * 1000).toISOString(), created: NOW / 1000, ...over,
});
const decided = (status: DecidedProposal["status"], over: Partial<DecidedProposal> = {}): DecidedProposal => ({ ...p(), status, refusal: [], refusal_text: [], ...over });

describe("ProposalCard", () => {
  it("shows direction, size, risk, levels, p/EV/spread and top features in plain words", () => {
    render(<ProposalCard p={p()} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByText("LONG")).toBeInTheDocument();
    expect(screen.getByText(/0.05 lots/)).toBeInTheDocument();
    expect(screen.getByText("$21.25")).toBeInTheDocument();          // an older engine: dollars, labelled USD
    expect(screen.getByText("(USD)")).toBeInTheDocument();
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

  it("says when the engine did not report the risk", () => {
    render(<ProposalCard p={p(60, { risk_usd: null })} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByTitle(/does not report the risk/)).toHaveTextContent("—");
  });

  const gbp: Partial<Proposal> = {
    lots: 0.01, currency: "GBP", risk_amount: 8.2, risk_fraction: 0.0082, min_lot_exception: true, risk_normal: 0.005,
    risk_cap: 0.01, reward_amount: 12.06, loss_left: 11.8, loss_left_period: "today",
  };

  it("shows risk in the account currency with its share of equity, and stop and target in money", () => {
    render(<ProposalCard p={p(60, gbp)} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByText("£8.20")).toBeInTheDocument();
    expect(screen.getByText(/0.82% of equity/)).toBeInTheDocument();
    expect(screen.getByText(/if the stop is hit, before costs/)).toBeInTheDocument();
    expect(screen.queryByText("(USD)")).toBeNull();
    expect(screen.getByText("−£8.20")).toBeInTheDocument();          // lost at the stop
    expect(screen.getByText("+£12.06")).toBeInTheDocument();         // won at the target
  });

  it("notes the minimum-lot exception in plain words", () => {
    render(<ProposalCard p={p(60, gbp)} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByText("Smallest trade size: above the normal 0.5%, within your 1% cap.")).toBeInTheDocument();
    render(<ProposalCard p={p(60, { ...gbp, proposal_id: "p2", min_lot_exception: false })} now={NOW} canDecide blockers={[]} />);
    expect(screen.getAllByText(/Smallest trade size/)).toHaveLength(1);
  });

  it("shows the loss budget left if this loses, spent, or unknown, and none on decided cards", () => {
    const { rerender } = render(<ProposalCard p={p(60, gbp)} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByText("£11.80 left today if this loses (open trades aside)")).toBeInTheDocument();
    rerender(<ProposalCard p={p(60, { ...gbp, loss_left: 0 })} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByText("No loss budget left today if this loses (open trades aside): new entries stop")).toBeInTheDocument();
    rerender(<ProposalCard p={p(60, { ...gbp, loss_left: null, loss_left_period: null })} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByText("Loss budget left today: unknown")).toBeInTheDocument();
    rerender(<ProposalCard p={{ ...decided("approved"), ...gbp }} now={NOW} canDecide blockers={[]} />);
    expect(screen.queryByText(/if this loses/)).toBeNull();
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
    ["submitted", { decided_channel: "dashboard" }, /Approval sent\. The engine re-checks risk.*via the dashboard/],
    ["approved", { decided_channel: "telegram" }, /Approved, order sent\..*via Telegram/],
    ["rejected", { reason_code: "cost" as const }, /Rejected · Cost/],
    ["refused", { refusal: ["owner_halt", "drift_halt"] }, /Refused at approval\. The RiskGate blocked the order \(owner halt, drift halt\); nothing was sent/],
    // BACKLOG 66: the gate allowed it, the MT5 bridge's notional ceiling refused it
    ["refused", { refusal: ["bridge_notional_ceiling"] }, /^Refused by the MT5 box\. Its safety limit stops open gold worth more than 6× equity, and this trade would have gone over it; nothing was sent\.$/],
    ["refused", { refusal: ["bridge_notional_ceiling_unreadable"] }, /^Refused by the MT5 box\. It couldn't read the account figures it checks, so it refused to be safe; nothing was sent\.$/],
    ["expired", {}, /Expired\./],
  ])("shows the %s outcome without actions", (status, over, copy) => {
    render(<ProposalCard p={decided(status as DecidedProposal["status"], over)} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent(copy);
    expect(screen.queryByRole("button")).toBeNull();
  });

  // docs/ux/order-not-placed.md: the API words the MT5 box's and the broker's refusals (`not_placed`)
  const margin = { by: "broker" as const, reason: "there wasn't enough free margin", code: null, next_step: "Check free margin on the Overview before approving the next trade.", nothing_sent: true, not_sent: false };
  const stops = { by: "broker" as const, reason: "the stop or target wasn't accepted (too close to the price)", code: null, next_step: "x", nothing_sent: true, not_sent: false };
  const stale = { by: "mt5_box" as const, reason: "it took more than 10 s to check the order, so it dropped it rather than trade on an old price", code: null, next_step: "x", nothing_sent: true, not_sent: false };
  const generic = { by: "broker" as const, reason: null, code: 10006, next_step: "If it repeats, ask Claude to check (broker code 10006).", nothing_sent: true, not_sent: false };
  it.each([
    [{ refusal: ["broker_refused:10019"], not_placed: margin, decided_channel: "telegram" }, /^Refused by the broker\. There wasn't enough free margin; nothing was sent\. · via Telegram$/],
    [{ refusal: ["broker_refused:10016"], not_placed: stops }, /^Refused by the broker\. The stop or target wasn't accepted \(too close to the price\); nothing was sent\.$/],
    [{ refusal: ["bridge_stale_order"], not_placed: stale }, /^Refused by the MT5 box\. It took more than 10 s to check the order, so it dropped it rather than trade on an old price; nothing was sent\.$/],
    [{ refusal: ["broker_refused:10006"], not_placed: generic }, /^Refused by the broker \(code 10006\); nothing was sent\.$/],
  ])("shows an order the MT5 box or the broker refused as not placed, in the API's words (%#)", (over, copy) => {
    const { container } = render(<ProposalCard p={decided("refused", over)} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent(copy);
    expect(screen.getByRole("status")).toHaveClass("outcome", "bad");
    expect(container.querySelector("article")).toHaveAttribute("aria-label", "Long 0.05 lots XAUUSD, not placed");
  });

  // BACKLOG 85: the approval path failed before the order existed; the order could not be sent, nobody refused it
  const box = { by: "mt5_box" as const, reason: "the MT5 box couldn't be reached", code: null, next_step: "x", nothing_sent: true, not_sent: true };
  const engine = { by: "engine" as const, reason: "the engine hit an error before sending it", code: null, next_step: "x", nothing_sent: true, not_sent: true };
  it.each([
    [{ refusal: ["not_sent:mt5_box"], not_placed: box }, /^Could not be sent\. The MT5 box couldn't be reached; nothing was sent\.$/],
    [{ refusal: ["not_sent:engine"], not_placed: engine }, /^Could not be sent\. The engine hit an error before sending it; nothing was sent\.$/],
  ])("shows an approval that could not be sent as not placed, never as refused (%#)", (over, copy) => {
    render(<ProposalCard p={decided("refused", over)} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent(copy);
    expect(screen.getByRole("status")).toHaveClass("outcome", "bad");
  });

  it("never says nothing was sent for the paper broker's duplicate, whose first send went out", () => {
    const dup = { by: "broker" as const, reason: "it was already sent; this repeat was refused", code: null, next_step: "x", nothing_sent: false, not_sent: false };
    render(<ProposalCard p={decided("refused", { refusal: ["broker_refused:10027:paper"], not_placed: dup })} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent(/^Refused by the broker\. It was already sent; this repeat was refused\.$/);
  });

  it("falls back to the broker's code from an older API without not_placed, never blaming the RiskGate", () => {
    render(<ProposalCard p={decided("refused", { refusal: ["broker_refused:10019"] })} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent(/^Refused by the broker \(code 10019\); nothing was sent\.$/);
    expect(screen.getByRole("status")).not.toHaveTextContent("The RiskGate blocked the order");
  });

  it("names only the decision channel, never who decided, and says nothing when the channel is unknown", () => {
    const { rerender } = render(<ProposalCard p={decided("rejected", { reason_code: "news", decided_channel: "dashboard" })} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent(/^Rejected · News · via the dashboard$/);
    rerender(<ProposalCard p={decided("approved", { decided_channel: null })} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByRole("status")).not.toHaveTextContent("via");
  });

  // docs/ux/order-not-confirmed.md (BACKLOG 72): the API words the broker's silence (`not_confirmed`); amber while unknown
  const GATE = " New entries wait until it's settled.";
  const esc = (x: string) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const nc = (outcome: string, heading: string, text: string, tone: string) =>
    ({ refusal: ["not_confirmed"], not_confirmed: { outcome, heading, text, tone } }) as Partial<DecidedProposal>;
  it.each([
    ["pending", "Not confirmed.", "Sent, but the broker didn't confirm it. The engine looks it up at the broker and tells you what it finds, or tells you after 10 minutes that it still doesn't know." + GATE, "warn", "warn", "sent, not confirmed by the broker"],
    ["opened", "Opened.", "The broker confirmed it late; stop, target and exit are automatic.", "ok", "ok", "opened"],
    ["opened_closed", "Opened and already closed.", "The broker confirmed it late: it opened at 2400.40 and closed at 2396.00 (−£8.20). It's in the trade record.", "neutral", "muted-box", "opened and closed"],
    ["no_trade", "Not placed.", "Sent, but the broker has no trade for it (checked twice, at least 2 minutes apart); no trade was opened.", "bad", "bad", "not placed"],
    ["opened_late", "Opened after all.", "The broker confirmed it after it was reported not placed; stop, target and exit are automatic.", "ok", "ok", "opened"],
    ["still_unknown", "Still not confirmed.", "After 10 minutes the broker hasn't shown whether it opened. The engine keeps looking; check MT5 for an open XAUUSD trade." + GATE, "warn", "warn", "still not confirmed"],
  ])("shows an order the broker didn't confirm as %s, in the API's words", (outcome, heading, text, tone, cls, name) => {
    const status = outcome === "no_trade" ? "refused" : "approved";
    const { container } = render(
      <ProposalCard p={decided(status, { ...nc(outcome, heading, text, tone), decided_channel: "telegram" })} now={NOW} canDecide blockers={[]} />);
    const note = screen.getByRole("status");
    expect(note).toHaveTextContent(new RegExp(`^${esc(heading)} ${esc(text)} · via Telegram$`));
    expect(note).toHaveClass("outcome", cls);
    expect(note.querySelector("strong")).toHaveTextContent(heading);
    expect(container.querySelector("article")).toHaveAttribute("aria-label", `Long 0.05 lots XAUUSD, ${name}`);
    expect(note).not.toHaveTextContent(/APPROVED|Approved, order sent|not_confirmed|retcode/);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("falls back to the approved note from an older API without not_confirmed", () => {
    render(<ProposalCard p={decided("approved")} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent(/^Approved, order sent\. Stop, target and exit are automatic\.$/);
  });

  it("words a refusal at approval for an unconfirmed order in the API's words, never the code", () => {
    render(<ProposalCard p={decided("refused", { refusal: ["unconfirmed_order_pending"], refusal_text: ["an earlier order isn't confirmed by the broker yet"] })} now={NOW} canDecide blockers={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent(/^Refused at approval\. The RiskGate blocked the order \(an earlier order isn't confirmed by the broker yet\); nothing was sent\.$/);
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
