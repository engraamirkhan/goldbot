import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { Home } from "./Home";

const status = (over: Partial<apiModule.Status> = {}): apiModule.Status => ({
  mode: "propose", halted: false, halted_by: null, halt_reason: null, pending: 0, supervisor: {},
  supervisor_halt: false, supervisor_reasons: [], drift_halt: false, drift_reasons: [], blackout: null, ...over,
});

const EXITS = "Exits keep running: open trades keep their stop, target and automatic exits.";
const account = (entry: Partial<apiModule.EntryStatus> = {}): apiModule.AccountSummary => ({
  account_id: "icm-demo", broker: "icm", mode: "demo", equity: 1000, day_pnl_pct: 0, week_pnl_pct: 0, drawdown_pct: 0,
  stage: "normal", open_positions: 0, account_class: "raw",
  entry_status: { state: "open", headline: "Can take new trades", blocks: [], notes: [], as_of: new Date().toISOString(), exits: EXITS, ...entry },
});

const today = (): apiModule.TodayView => ({ as_of: "2026-10-19T10:00:00Z", accounts: [{
  account: "icm-demo", account_label: "icm-demo", mode: "demo", currency: "GBP", day_pnl: 12, open_pnl: 2, closed_pnl: 4, closed_n: 2,
  closed_complete: true, costs: { spread: 0.16, spread_complete: false, commission: 0.8, swap: -0.3 },
  loss_meter: { used: 6, binding: "probe_stop", limits: [
    { key: "probe_stop", label: "probe stop", limit: 16, remaining: 10 },
    { key: "daily_limit", label: "daily loss limit", limit: 50, remaining: 44 }] },
  age_s: 3, stale: false }] });

const proposal = (id = "p1", acct = "icm-demo", ms = 60_000): apiModule.Proposal => ({
  proposal_id: id, account_id: acct, agent_id: "box_break_close-g0", side: "long", lots: 0.05,
  entry: 2400.25, stop: 2396.0, target: 2406.5, p: 0.62, ev_r: 0.31, spread_points: 22, risk_usd: 21.25, min_lot_exception: false,
  top_features: [], expires_at: new Date(Date.now() + ms).toISOString(), created: 1760000000.125,
});

const mockIfFree = <K extends keyof typeof apiModule.api>(k: K, v: unknown) => {
  if (!vi.isMockFunction(apiModule.api[k])) vi.spyOn(apiModule.api, k).mockResolvedValue(v as never);
};

function renderHome(role: apiModule.Role = "viewer", props: Parameters<typeof Home>[0] = {}) {
  apiModule.setUser({ email: "a@x.io", role, signed_in_with: "password_totp" });
  mockIfFree("status", status());
  mockIfFree("accounts", [account()]);
  mockIfFree("proposals", []);
  mockIfFree("recentProposals", []);
  mockIfFree("feeds", [{ account_id: "icm-demo", last_tick_age_s: 2, spread_points: 20, terminal_connected: true,
    webhook_p99_latency_s: null, supervisor_heartbeat_age_s: 3 }]);
  mockIfFree("today", today());
  mockIfFree("openPositions", { as_of: "2026-10-19T10:00:00Z", unreadable: [], positions: [] });
  mockIfFree("strategyWatch", { as_of: "2026-10-19T10:00:00Z", unreadable: [], agents: [] });
  mockIfFree("tradesOverview", { as_of: "2026-10-19T10:00:00Z", periods: [], unreadable: [] });
  mockIfFree("activity", { events: [], unreadable: [] });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><Home {...props} /></QueryClientProvider>);
}

afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

describe("Home is a calm status page", () => {
  it("has five cards in order and nothing about trades beyond a link", async () => {
    renderHome();
    await screen.findByRole("group", { name: "Today icm-demo" });
    const order = ["Needs you", "System status", "Today", "Your strategies", "Learning"].map((name) => screen.getByRole("region", { name }));
    for (let i = 1; i < order.length; i++) {
      expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    for (const gone of ["Activity feed", "Trades summary", "Open trades", "MT5 chart"]) expect(screen.queryByRole("region", { name: gone })).toBeNull();
    expect(screen.queryByLabelText("Safety state")).toBeNull();                    // safety is said once: the header pill
    expect(screen.queryByRole("button", { name: "Halt new entries" })).toBeNull();
  });

  it("says nothing needs the owner when no trade waits and every account is open", async () => {
    renderHome();
    expect(await screen.findByRole("heading", { name: "Nothing needs you" })).toBeInTheDocument();
    expect(screen.getByText(/You'll get a Telegram ping/)).toBeInTheDocument();
  });

  it("shows a placeholder the size of the content while loading", async () => {
    vi.spyOn(apiModule.api, "accounts").mockReturnValue(new Promise(() => {}));
    renderHome();
    expect(await screen.findByLabelText("Loading entry status")).toHaveAttribute("aria-busy", "true");
  });
});

describe("Home needs you", () => {
  it("shows the waiting trade itself with Approve, and how many more wait on Trades", async () => {
    vi.spyOn(apiModule.api, "proposals").mockResolvedValue([proposal("p1"), proposal("p2", "icm-demo", 80_000)]);
    const onOpenTrades = vi.fn();
    renderHome("approver", { onOpenTrades });
    expect(await screen.findByRole("heading", { name: "Trade waiting (1 of 2)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "1 more waiting →" }));
    expect(onOpenTrades).toHaveBeenCalled();
  });

  it("puts a waiting real-money proposal first, before a demo one", async () => {
    vi.spyOn(apiModule.api, "proposals").mockResolvedValue([proposal("demo1", "icm-demo", 20_000), proposal("real1", "icm-live", 80_000)]);
    vi.spyOn(apiModule.api, "accounts").mockResolvedValue([{ ...account(), account_id: "icm-live", mode: "live" }, account()]);
    const { container } = renderHome("approver");
    expect(await screen.findByRole("heading", { name: "Trade waiting (1 of 2)" })).toBeInTheDocument();
    await vi.waitFor(() => expect(container.querySelector(".home-needs")).toHaveTextContent("icm-live"));
  });

  it("lists only the accounts that can't trade, with the reason and the fix", async () => {
    vi.spyOn(apiModule.api, "accounts").mockResolvedValue([
      { ...account({ state: "blocked", headline: "No new trades: Broker clock differs", blocks: [{ code: "server_clock_mismatch",
        scope: "account", text: "Broker clock differs from the expected time zone", fix: "Check the server time zone.", kind: "act" }] }) },
      { ...account(), account_id: "icm-other" }]);
    renderHome();
    const row = await screen.findByRole("article", { name: "Entry status icm-demo" });
    expect(row).toHaveTextContent("Needs you");
    expect(row).toHaveTextContent("Check the server time zone.");
    expect(screen.queryByRole("article", { name: "Entry status icm-other" })).toBeNull();
  });

  it("says no engine is reporting when none is", async () => {
    vi.spyOn(apiModule.api, "accounts").mockResolvedValue([]);
    renderHome();
    expect(await screen.findByText(/No engine reporting, so no new trades can open/)).toBeInTheDocument();
  });
});

describe("Home system status line", () => {
  it("says engine, prices and market in words", async () => {
    renderHome();
    const line = await screen.findByRole("region", { name: "System status" });
    await vi.waitFor(() => expect(line).toHaveTextContent("Engine running · 1 account"));
    expect(line).toHaveTextContent("Prices fresh · 2 s old");
    expect(line).toHaveTextContent("Market open");
  });
});

describe("Home today", () => {
  it("is one line per account with its DEMO badge, closed and open, and the limit that binds", async () => {
    renderHome();
    const row = await screen.findByRole("group", { name: "Today icm-demo" });
    expect(row).toHaveTextContent("DEMO");
    expect(row).toHaveTextContent("+£12.00 today after costs");
    expect(row).toHaveTextContent("closed +£4.00 (2 trades) · open +£2.00");
    expect(row).toHaveTextContent("£10.00 left before the probe stop (£16.00)");
  });

  it("marks money older than 60 s as stale", async () => {
    const v = today();
    v.accounts[0] = { ...v.accounts[0], stale: true, age_s: 95 };
    vi.spyOn(apiModule.api, "today").mockResolvedValue(v);
    renderHome();
    expect(await screen.findByText(/95 s old \(over 60 s\)/)).toBeInTheDocument();
  });

  it("links to the open trades on the Trades screen", async () => {
    vi.spyOn(apiModule.api, "openPositions").mockResolvedValue({ as_of: "2026-10-19T10:00:00Z", unreadable: [], positions: [
      { account: "icm-demo", mode: "demo", probe: false, position_id: 1, agent_id: "a", family: "box_break_close", timeframe: "1h",
        side: "long", lots: 0.01, entry: 2400, stop: 2396, target: 2410, opened_utc: "2026-10-19T09:00:00Z", price: 2401,
        floating_pnl: 1, currency: "GBP", r_now: 0.25, age_s: 2, stale: false, account_label: "icm-demo", family_label: "", kind: "strategy" },
      { account: "icm-demo", mode: "demo", probe: true, position_id: 2, agent_id: "b", family: "probe", timeframe: null,
        side: "short", lots: 0.01, entry: 2400, stop: 2404, target: 2390, opened_utc: "2026-10-19T09:30:00Z", price: 2401,
        floating_pnl: -1, currency: "GBP", r_now: -0.25, age_s: 2, stale: false, account_label: "icm-demo", family_label: "", kind: "cost_probe" }] });
    const onOpenTrades = vi.fn();
    renderHome("viewer", { onOpenTrades });
    fireEvent.click(await screen.findByRole("button", { name: "2 open trades →" }));
    expect(onOpenTrades).toHaveBeenCalled();
  });

  it("keeps the other cards when one source fails", async () => {
    vi.spyOn(apiModule.api, "today").mockRejectedValue(new Error("down"));
    renderHome();
    expect(await screen.findByText("Could not load today's money.")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Nothing needs you" })).toBeInTheDocument();
  });
});

describe("Home when the waiting trades fail to load", () => {
  it("shows an amber Try again state saying Telegram still works, not a red error", async () => {
    const proposals = vi.spyOn(apiModule.api, "proposals").mockRejectedValue(new Error("down"));
    const { container } = renderHome();
    expect(await screen.findByText("Could not load waiting trades.")).toBeInTheDocument();
    expect(screen.getByText("Telegram approvals still work.")).toBeInTheDocument();
    expect(container.querySelector(".err")).toBeNull();
    expect(container.querySelector(".state-box.error")).not.toBeNull();
    const calls = proposals.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await vi.waitFor(() => expect(proposals.mock.calls.length).toBeGreaterThan(calls));
  });
});

describe("Home strategies and learning", () => {
  it("says each strategy's state in words and calls an unknown state unknown", async () => {
    vi.spyOn(apiModule.api, "strategyWatch").mockResolvedValue({ as_of: "2026-10-19T10:00:00Z", unreadable: [], agents: [
      { agent_id: "box_break_close-g0-1h", family: "box_break_close", timeframe: "1h", state: "in_trade",
        text: "in a trade (shadow): long from 2,400.00, stop 2,394.00, target 2,412.00", signal_utc: "2026-10-19T08:00:00Z" },
      { agent_id: "box_break_close-g0-4h", family: "box_break_close", timeframe: "4h", state: "unknown",
        text: "no signal recorded yet; the engine does not publish what it watches" }] });
    renderHome();
    const list = await screen.findByRole("list", { name: "Strategy states" });
    expect(list).toHaveTextContent("Box break close 1hin a tradein a trade (shadow): long from 2,400.00");
    expect(list).toHaveTextContent("Box break close 4hunknown");
  });

  it("puts the learning in one line, in R with n, tagged shadow, not money", async () => {
    renderHome("viewer", { onOpenProgress: () => {} });
    const card = await screen.findByRole("region", { name: "Learning" });
    await vi.waitFor(() => expect(card).toHaveTextContent("Shadow trades, 7 days: every candidate"));
    expect(card).toHaveTextContent("shadow, not money");
    expect(card).toHaveTextContent("Nothing changed enough to report since last week.");
    expect(screen.getByRole("button", { name: "Road to live ›" })).toBeInTheDocument();
  });
});
