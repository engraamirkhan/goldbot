import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { Home } from "./Home";

const status = (over: Partial<apiModule.Status>): apiModule.Status => ({
  mode: "propose", halted: false, halted_by: null, halt_reason: null, pending: 0, supervisor: {},
  supervisor_halt: false, supervisor_reasons: [], drift_halt: false, drift_reasons: [], blackout: null, ...over,
});

function renderHome(role: apiModule.Role) {
  apiModule.setUser({ email: "a@x.io", role, signed_in_with: "password_totp" });
  vi.spyOn(apiModule.api, "accounts").mockResolvedValue([]);
  vi.spyOn(apiModule.api, "automode").mockRejectedValue(new Error("not under test"));   // AutoMode.test.tsx covers the card
  if (!vi.isMockFunction(apiModule.api.activity)) vi.spyOn(apiModule.api, "activity").mockResolvedValue({ events: [], unreadable: [] });
  if (!vi.isMockFunction(apiModule.api.tradesOverview)) vi.spyOn(apiModule.api, "tradesOverview").mockResolvedValue({ as_of: "2026-10-19T10:00:00Z", periods: [], unreadable: [] });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><Home /></QueryClientProvider>);
}

describe("Home accounts", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

  it("shows a loading placeholder until the accounts arrive", async () => {
    apiModule.setUser({ email: "a@x.io", role: "viewer", signed_in_with: "password_totp" });
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    vi.spyOn(apiModule.api, "accounts").mockReturnValue(new Promise(() => {}));
    vi.spyOn(apiModule.api, "automode").mockRejectedValue(new Error("not under test"));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><Home /></QueryClientProvider>);
    expect(await screen.findByLabelText("Loading account")).toHaveAttribute("aria-busy", "true");
  });
});

describe("Home entry status", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

  it("puts the entry status first, one line per account from the API", async () => {
    apiModule.setUser({ email: "a@x.io", role: "viewer", signed_in_with: "password_totp" });
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    vi.spyOn(apiModule.api, "automode").mockRejectedValue(new Error("not under test"));
    vi.spyOn(apiModule.api, "accounts").mockResolvedValue([{
      account_id: "icm-demo", broker: "icm", mode: "demo", equity: 1000, day_pnl_pct: 0, week_pnl_pct: 0, drawdown_pct: 0,
      stage: "normal", open_positions: 0, account_class: "raw",
      entry_status: { state: "open", headline: "Can take new trades", blocks: [], notes: [], as_of: null,
                      exits: "Exits keep running: open trades keep their stop, target and automatic exits." } }]);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(<QueryClientProvider client={qc}><Home /></QueryClientProvider>);
    const row = await screen.findByRole("article", { name: "Entry status icm-demo" });
    expect(row).toHaveTextContent("Can take new trades");
    expect(container.querySelector(".entry")).not.toBeNull();
  });

  it("shows the empty state when no engine reports", async () => {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    renderHome("viewer");
    expect(await screen.findByText(/No engine is reporting, so no new trades can open/)).toBeInTheDocument();
  });
});

describe("Home waiting trade", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });
  const proposal = (): apiModule.Proposal => ({
    proposal_id: "p1", account_id: "icm-demo", agent_id: "session_open-g0-abc", side: "long", lots: 0.05,
    entry: 2400.25, stop: 2396.0, target: 2406.5, p: 0.62, ev_r: 0.31, spread_points: 22, risk_usd: 21.25, min_lot_exception: false,
    top_features: [], expires_at: new Date(Date.now() + 60_000).toISOString(), created: 1760000000.125,
  });

  it("shows the waiting trade itself with Approve", async () => {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    vi.spyOn(apiModule.api, "proposals").mockResolvedValue([proposal()]);
    vi.spyOn(apiModule.api, "recentProposals").mockResolvedValue([]);
    renderHome("approver");
    expect(await screen.findByRole("heading", { name: "Trade waiting" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("says no trade is waiting, and that Telegram will ping, when the list is empty", async () => {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    vi.spyOn(apiModule.api, "proposals").mockResolvedValue([]);
    vi.spyOn(apiModule.api, "recentProposals").mockResolvedValue([]);
    renderHome("viewer");
    expect(await screen.findByText(/No trade waiting\. You'll get a Telegram ping/)).toBeInTheDocument();
  });
});

describe("Home says safety once", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

  it("has no safety strip and no halt form: the entry status block and the header pill carry them", async () => {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    vi.spyOn(apiModule.api, "proposals").mockResolvedValue([]);
    vi.spyOn(apiModule.api, "recentProposals").mockResolvedValue([]);
    renderHome("owner");
    expect(await screen.findByRole("heading", { name: "Entry status" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Safety state")).toBeNull();
    expect(screen.queryByRole("button", { name: "Halt new entries" })).toBeNull();
  });
});

describe("Home activity feed", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

  it("shows what the system is doing on the front page", async () => {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    vi.spyOn(apiModule.api, "proposals").mockResolvedValue([]);
    vi.spyOn(apiModule.api, "recentProposals").mockResolvedValue([]);
    vi.spyOn(apiModule.api, "activity").mockResolvedValue({ unreadable: [], events: [
      { ts: "2026-10-19T10:00:00Z", kind: "shadow_opened", agent_id: "box_break_close-g0", timeframe: "1h", side: "long", entry: 2400 },
    ] });
    renderHome("viewer");
    expect(await screen.findByRole("heading", { name: "Activity" })).toBeInTheDocument();
    expect(await screen.findByRole("list", { name: "Activity" })).toHaveTextContent("Shadow opened");
  });

  it("summarises every type of trade under the feed", async () => {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    vi.spyOn(apiModule.api, "proposals").mockResolvedValue([]);
    vi.spyOn(apiModule.api, "recentProposals").mockResolvedValue([]);
    renderHome("viewer");
    expect(await screen.findByRole("heading", { name: "Trades summary" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "7 days" })).toBeInTheDocument();
  });
});

describe("Home status card stack", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });
  const today = (): apiModule.TodayView => ({ as_of: "2026-10-19T10:00:00Z", accounts: [{
    account: "icm-demo", mode: "demo", currency: "GBP", day_pnl: 12, open_pnl: 2, closed_pnl: 4, closed_n: 2,
    closed_complete: true, costs: { spread: 0.16, spread_complete: false, commission: 0.8, swap: -0.3 },
    loss_meter: { used: 6, binding: "probe_stop", limits: [
      { key: "probe_stop", label: "probe stop", limit: 16, remaining: 10 },
      { key: "daily_limit", label: "daily loss limit", limit: 50, remaining: 44 }] },
    age_s: 3, stale: false }] });
  function base() {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    vi.spyOn(apiModule.api, "proposals").mockResolvedValue([]);
    vi.spyOn(apiModule.api, "recentProposals").mockResolvedValue([]);
  }

  it("shows the cards in the decided order: can it trade, today, open trades, strategies, learning, activity, summary", async () => {
    base();
    vi.spyOn(apiModule.api, "today").mockResolvedValue(today());
    renderHome("viewer");
    await screen.findByRole("group", { name: "Today icm-demo" });
    const order = ["Entry status", "Today", "Open trades", "Your strategies", "Learning", "Activity feed", "Trades summary"]
      .map((name) => screen.getByRole("region", { name }));
    for (let i = 1; i < order.length; i++) {
      expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("splits today's money into closed and open after costs, with a DEMO badge and the probe stop named", async () => {
    base();
    vi.spyOn(apiModule.api, "today").mockResolvedValue(today());
    renderHome("viewer");
    const row = await screen.findByRole("group", { name: "Today icm-demo" });
    expect(row).toHaveTextContent("DEMO");
    expect(row).toHaveTextContent("Day P&L after costs+£12.00");
    expect(row).toHaveTextContent("Closed+£4.00");
    expect(row).toHaveTextContent("Open+£2.00");
    expect(row).toHaveTextContent("commission £0.80 · swap −£0.30");
    expect(row).toHaveTextContent("£10.00 left before the probe stop (£16.00)");
  });

  it("marks money older than 60 s as stale", async () => {
    base();
    const v = today();
    v.accounts[0] = { ...v.accounts[0], stale: true, age_s: 95 };
    vi.spyOn(apiModule.api, "today").mockResolvedValue(v);
    renderHome("viewer");
    expect(await screen.findByText(/95 s old \(over 60 s\)/)).toBeInTheDocument();
  });

  it("keeps the other cards when one source fails", async () => {
    base();
    vi.spyOn(apiModule.api, "today").mockRejectedValue(new Error("down"));
    vi.spyOn(apiModule.api, "openPositions").mockResolvedValue({ as_of: "2026-10-19T10:00:00Z", unreadable: [], positions: [{
      account: "icm-live", mode: "live", probe: false, position_id: 1, agent_id: "box_break_close-g0", family: "box_break_close",
      timeframe: "1h", side: "long", lots: 0.05, entry: 2400, stop: 2396, target: 2420, opened_utc: "2026-10-19T09:00:00Z",
      price: 2410, floating_pnl: 40, currency: "GBP", r_now: 2.5, age_s: 2, stale: false }] });
    renderHome("viewer");
    expect(await screen.findByText("Could not load today's money.")).toBeInTheDocument();
    const open = await screen.findByRole("list", { name: "Open trades" });
    expect(open).toHaveTextContent("REAL");
    expect(open).toHaveTextContent("+£40.00 · +2.50 R");
    expect(open).toHaveTextContent("entry 2,400.00 · stop 2,396.00 · target 2,420.00 USD/oz");
  });

  it("says each strategy's state in words and calls an unknown state unknown", async () => {
    base();
    vi.spyOn(apiModule.api, "strategyWatch").mockResolvedValue({ as_of: "2026-10-19T10:00:00Z", unreadable: [], agents: [
      { agent_id: "box_break_close-g0-1h", family: "box_break_close", timeframe: "1h", state: "in_trade",
        text: "in a trade (shadow): long from 2,400.00, stop 2,394.00, target 2,412.00", signal_utc: "2026-10-19T08:00:00Z" },
      { agent_id: "box_break_close-g0-4h", family: "box_break_close", timeframe: "4h", state: "unknown",
        text: "no signal recorded yet; the engine does not publish what it watches" }] });
    renderHome("viewer");
    const list = await screen.findByRole("list", { name: "Strategy states" });
    expect(list).toHaveTextContent("Box break close 1hin a tradein a trade (shadow): long from 2,400.00");
    expect(list).toHaveTextContent("Box break close 4hunknown");
  });

  it("puts a waiting real-money proposal in card 1 before a demo one", async () => {
    vi.spyOn(apiModule.api, "status").mockResolvedValue(status({}));
    const p = (id: string, account: string, ms: number): apiModule.Proposal => ({
      proposal_id: id, account_id: account, agent_id: "box_break_close-g0", side: "long", lots: 0.05, entry: 2400,
      stop: 2396, target: 2406, p: 0.6, ev_r: 0.3, spread_points: 20, risk_usd: 20, min_lot_exception: false,
      top_features: [], expires_at: new Date(Date.now() + ms).toISOString(), created: 1760000000 });
    vi.spyOn(apiModule.api, "proposals").mockResolvedValue([p("demo1", "icm-demo", 20_000), p("real1", "icm-live", 80_000)]);
    vi.spyOn(apiModule.api, "recentProposals").mockResolvedValue([]);
    apiModule.setUser({ email: "a@x.io", role: "approver", signed_in_with: "password_totp" });
    vi.spyOn(apiModule.api, "accounts").mockResolvedValue([{ account_id: "icm-live", mode: "live" } as apiModule.AccountSummary,
      { account_id: "icm-demo", mode: "demo" } as apiModule.AccountSummary]);
    vi.spyOn(apiModule.api, "automode").mockRejectedValue(new Error("not under test"));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(<QueryClientProvider client={qc}><Home /></QueryClientProvider>);
    expect(await screen.findByRole("heading", { name: "Trade waiting (1 of 2)" })).toBeInTheDocument();
    await vi.waitFor(() => expect(container.querySelector(".home-needs")).toHaveTextContent("icm-live"));
  });
});
