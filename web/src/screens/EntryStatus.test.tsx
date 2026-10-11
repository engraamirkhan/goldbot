import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AccountSummary, EntryStatus } from "../lib/api";
import { EntryStatusPanel } from "./EntryStatus";

const es = (over: Partial<EntryStatus> = {}): EntryStatus => ({
  state: "open", headline: "Can take new trades", blocks: [], notes: [],
  exits: "Exits keep running: open trades keep their stop, target and automatic exits.", as_of: "2026-10-14T13:59:58Z", ...over,
});
const acct = (entry_status: EntryStatus | null, over: Partial<AccountSummary> = {}): AccountSummary => ({
  account_id: "icm-demo", broker: "icm", mode: "demo", equity: 1000, day_pnl_pct: 0, week_pnl_pct: 0, drawdown_pct: 0,
  stage: "normal", open_positions: 0, account_class: "raw", entry_status, ...over,
});
const blk = (over: Partial<EntryStatus["blocks"][number]>) => ({
  code: "market_closed", text: "Market closed", fix: "Nothing to do: it reopens by itself.", kind: "wait" as const,
  scope: "account" as const, agent_id: null, at: null, at_label: null, runbook: null, runbook_url: null, ...over,
});
const row = () => screen.getByRole("article", { name: "Entry status icm-demo" });

describe("EntryStatusPanel", () => {
  it("holds its place with a busy placeholder while loading", () => {
    render(<EntryStatusPanel loading />);
    expect(screen.getByRole("heading", { name: "Entry status" })).toBeInTheDocument();
    expect(screen.getByLabelText("Loading entry status")).toHaveAttribute("aria-busy", "true");
  });

  it("says when the server cannot be reached, and that exits do not depend on the dashboard", () => {
    render(<EntryStatusPanel error />);
    expect(screen.getByRole("alert")).toHaveTextContent("Entry status unavailable");
    expect(screen.getByRole("alert")).toHaveTextContent("Exits keep running on the engine");
  });

  it("says nothing can trade when no engine reports", () => {
    render(<EntryStatusPanel accounts={[]} />);
    expect(screen.getByText(/No engine is reporting, so no new trades can open/)).toBeInTheDocument();
  });

  it("shows an open account with a word badge, its headline and the exits line", () => {
    render(<EntryStatusPanel accounts={[acct(es())]} />);
    expect(within(row()).getByText("Open")).toHaveClass("entry-badge");
    expect(row()).toHaveClass("ok");
    expect(within(row()).getByText("Can take new trades")).toBeInTheDocument();
    expect(within(row()).getByText(/^Exits keep running/)).toBeInTheDocument();
    expect(within(row()).getByText(/^as of/)).toHaveAttribute("title", "2026-10-14 13:59 UTC");
  });

  it("says when a paused account reopens in the owner's time, UTC on hover", () => {
    const opens = "2026-10-18T22:02:00Z";
    render(<EntryStatusPanel now={Date.parse("2026-10-17T10:00:00Z")} accounts={[acct(es({
      state: "waiting", headline: "No new trades for now: Market closed",
      blocks: [blk({ at: opens, at_label: "opens" })] }))]} />);
    expect(within(row()).getByText("Paused")).toBeInTheDocument();
    expect(row()).toHaveClass("warn");
    const when = within(row()).getByText(/^opens .* your time \(in 1 d 12 h\)$/);
    expect(when).toHaveAttribute("title", "2026-10-18 22:02 UTC");
    expect(within(row()).getByText("Nothing to do: it reopens by itself.")).toBeInTheDocument();
    expect(within(row()).queryByText("Needs you")).toBeNull();
  });

  it("marks what needs the owner in words and links its Runbook section", () => {
    const url = "https://github.com/engraamirkhan/goldbot/blob/main/docs/RUNBOOK.md#server-clock-us-eu-dst-gap-weeks";
    render(<EntryStatusPanel accounts={[acct(es({
      state: "blocked", headline: "No new trades: Broker clock differs from the expected time zone",
      blocks: [blk({ code: "server_clock_mismatch", text: "Broker clock differs from the expected time zone", kind: "act",
                     fix: "See the Runbook: Server clock.", runbook: "Server clock (US-EU DST gap weeks)", runbook_url: url })] }))]} />);
    expect(within(row()).getByText("Blocked")).toBeInTheDocument();
    expect(row()).toHaveClass("bad");
    expect(within(row()).getByText("Needs you")).toBeInTheDocument();
    const link = within(row()).getByRole("link", { name: "Runbook: Server clock (US-EU DST gap weeks)" });
    expect(link).toHaveAttribute("href", url);
    expect(link).toHaveAttribute("rel", expect.stringContaining("noreferrer"));
  });

  it("names the strategy a partial block holds back and keeps notes apart", () => {
    render(<EntryStatusPanel accounts={[acct(es({
      state: "partial", headline: "Can take new trades, except trend-g0",
      notes: ["Price error 4 min ago; the engine carried on (4 since it started)."],
      blocks: [blk({ code: "throttle_loss_streak", text: "trend-g0 paused after 3 losses in a row", scope: "agent", agent_id: "trend-g0" })] }))]} />);
    expect(within(row()).getByText("Partly open")).toBeInTheDocument();
    expect(within(row()).getByText("trend-g0 paused after 3 losses in a row")).toBeInTheDocument();
    expect(within(row()).getByText(/carried on/)).toBeInTheDocument();
  });

  it("says so when this server does not report the status", () => {
    render(<EntryStatusPanel accounts={[acct(null)]} />);
    expect(within(row()).getByText(/not reported by this server/)).toBeInTheDocument();
  });
});
