import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AccountSummary } from "../lib/api";
import { BalanceCard } from "./BalanceCard";

const rule = "the smallest trade (minimum lot) risks at most 0.60% of equity at normal conviction; stop 1.5 x ATR(14) of the timeframe";
const base = (over: Partial<AccountSummary> = {}): AccountSummary => ({
  account_id: "icm-demo", broker: "icm", mode: "demo", equity: 990, day_pnl_pct: -0.01, week_pnl_pct: -0.01,
  drawdown_pct: 0.01, stage: "normal", open_positions: 1, account_class: "raw",
  balance: 1000, currency: "GBP", margin_level_pct: 641, day_pnl: -10,
  loss_budget: { limit: 15, used: 10, remaining: 5, binding: "combined 1.5% daily cap", period: "today", notes: [], unknown_paused: false, unknown_mixed: false,
                 binding_label: "all accounts together (1.5% of today's start)",
                 caps: [{ name: "account 2.0% daily cap", label: "this account (2.0% of today's start)", period: "today",
                          limit: 20, used: 10, remaining: 10 },
                        { name: "combined 1.5% daily cap", label: "all accounts together (1.5% of today's start)",
                          period: "today", limit: 15, used: 10, remaining: 5 }] },
  milestones: { rule, tradeable: ["15m"], next_timeframe: "1h", next_equity: 2744, progress: 990 / 2744, pending: null,
                rungs: [{ timeframe: "15m", equity_needed: 900, reached: true, pending: null },
                        { timeframe: "1h", equity_needed: 2744, reached: false, pending: null },
                        { timeframe: "4h", equity_needed: 5450, reached: false, pending: null },
                        { timeframe: "1d", equity_needed: 13160, reached: false, pending: null }] },
  ...over,
});

describe("BalanceCard", () => {
  it("shows balance and equity in the account currency with today's change and margin level", () => {
    render(<BalanceCard a={base()} />);
    const card = screen.getByRole("region", { name: "Account icm-demo" });
    expect(within(card).getByText("Balance").nextSibling).toHaveTextContent("£1,000.00");
    expect(within(card).getByText("Equity").nextSibling).toHaveTextContent("£990.00");
    expect(within(card).getByText("Equity").nextSibling).toHaveTextContent("−£10.00 open");
    expect(within(card).getByText("Today").nextSibling).toHaveTextContent("−£10.00");
    expect(within(card).getByText("Today").nextElementSibling?.querySelector("small")).toHaveAttribute("title", "The risk day starts at 00:00 UTC");
    expect(within(card).getByText("Margin level").nextSibling).toHaveTextContent("641%");
  });

  it("shows today's loss budget left, which cap binds, and a meter of what is used", () => {
    render(<BalanceCard a={base()} />);
    expect(screen.getByText("£5.00 left")).toBeInTheDocument();
    expect(screen.getByText("Limit set by: all accounts together (1.5% of today's start)")).toBeInTheDocument();
    expect(screen.getByText(/limit £15.00 today/)).toBeInTheDocument();
    const meter = screen.getByRole("meter", { name: "Daily loss budget used" });
    expect(meter).toHaveAttribute("aria-valuenow", "10");
    expect(meter).toHaveAttribute("aria-valuemax", "15");
    expect(screen.getByText(/exits always run/)).toBeInTheDocument();
  });

  it("says entries stop when the budget is spent, in red", () => {
    render(<BalanceCard a={base({ loss_budget: { limit: 15, used: 16, remaining: 0, binding: "combined 1.5% daily cap", binding_label: "all accounts together (1.5% of today's start)", period: "today", notes: [], caps: [], unknown_paused: false, unknown_mixed: false } })} />);
    expect(screen.getByText("£0.00 left").closest(".budget")).toHaveClass("bad");
    expect(screen.getByText(/New entries are stopped while today's loss is at the limit/)).toBeInTheDocument();
  });

  it("shows the next milestone with a progress bar and what trades now", () => {
    render(<BalanceCard a={base()} />);
    expect(screen.getByText("Your balance is big enough for 15-minute trades.")).toBeInTheDocument();
    expect(screen.getByText(/Next: 1-hour trades at £2,744.00/)).toBeInTheDocument();
    expect(screen.getByText("How this is worked out").closest("details")).toHaveTextContent("at normal conviction");
    const bar = screen.getByRole("progressbar", { name: "Progress to 1-hour trades" });
    expect(bar).toHaveAttribute("aria-valuenow", "36");
  });

  it("says pending broker terms instead of guessing amounts", () => {
    render(<BalanceCard a={base({ milestones: { rule, tradeable: [], next_timeframe: null, next_equity: null, progress: null,
                                                  pending: "pending broker terms", rungs: [] } })} />);
    expect(screen.getByText(/pending broker terms/)).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("falls back to equity alone for an engine that does not publish the account view", () => {
    render(<BalanceCard a={base({ balance: null, currency: null, margin_level_pct: null, day_pnl: null, loss_budget: null, milestones: null })} />);
    expect(screen.getByText("Equity").nextSibling).toHaveTextContent("990.00");
    expect(screen.getByText("Balance").nextSibling).toHaveTextContent("not reported");
    expect(screen.getByText("Margin level").nextSibling).toHaveTextContent("not reported");
    expect(screen.getByText(/Loss budget not reported/)).toBeInTheDocument();
    expect(screen.queryByRole("meter")).toBeNull();
  });

  it("says when the balance is not yet big enough for any timeframe and when a weekly cap decides", () => {
    render(<BalanceCard a={base({
      milestones: { rule, tradeable: [], next_timeframe: "15m", next_equity: 1366, progress: 0.72, pending: null, rungs: [] },
      loss_budget: { limit: 40, used: 38, remaining: 0, binding: "combined 4.0% weekly cap", period: "this week", notes: [],
                     binding_label: "all accounts together this week (4.0% of the week's start)", caps: [], unknown_paused: false, unknown_mixed: false } })} />);
    expect(screen.getByText("Your balance is not yet big enough for the smallest trade on any timeframe.")).toBeInTheDocument();
    expect(screen.getByText(/stopped while this week's loss is at the limit/)).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "Weekly loss budget used" })).toBeInTheDocument();
  });

  const cantCheck = (unknown: string, paused: boolean) => base({
    loss_budget: { limit: 20, used: 10, remaining: 10, binding: "account 2.0% daily cap", period: "today", notes: [],
                   binding_label: "this account (2.0% of today's start)", caps: [], unknown, unknown_paused: paused, unknown_mixed: false } });

  it("says it can't check the budget, in red with the reason and no roomier amount, while entries are paused", () => {
    const why = "The safety supervisor hasn't reported for over a minute, so the combined loss cap can't be checked. New entries are paused; exits always run.";
    render(<BalanceCard a={cantCheck(why, true)} />);
    const budget = screen.getByText("Loss budget: can't check").closest(".budget");
    expect(budget).toHaveClass("bad");
    expect(budget).toHaveTextContent(why);
    expect(budget).not.toHaveTextContent("£10.00 left");
    expect(budget).not.toHaveTextContent("£20.00");
    expect(screen.queryByRole("meter")).toBeNull();
  });

  it("says it can't check the budget in amber when the supervisor hasn't added up the accounts yet", () => {
    const why = "The safety supervisor hasn't added up the accounts yet, so the combined loss cap isn't shown. Exits always run.";
    render(<BalanceCard a={cantCheck(why, false)} />);
    const budget = screen.getByText("Loss budget: can't check").closest(".budget");
    expect(budget).toHaveClass("warn");
    expect(budget).toHaveTextContent(why);
    expect(budget).not.toHaveTextContent("£10.00 left");
  });

  it("says it can't check the budget in amber, with no amount, when the accounts report different currencies", () => {
    const why = "The accounts report different currencies (GBP, USD), so the combined loss cap can't be checked. Each account's own loss limit still applies; exits always run.";
    render(<BalanceCard a={base({ loss_budget: { ...cantCheck(why, false).loss_budget!, unknown_mixed: true } })} />);
    const budget = screen.getByText("Loss budget: can't check").closest(".budget");
    expect(budget).toHaveClass("warn");
    expect(budget).toHaveTextContent(why);
    expect(budget).not.toHaveTextContent("£10.00 left");
    expect(budget).not.toHaveTextContent("£20.00");
    expect(screen.queryByRole("meter")).toBeNull();
  });
});
