import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import type { Route } from "../lib/route";
import { bars, blindedPage, board, openPositions, todayAccount, tradeRow, tradesPage } from "../test/tradesFixtures";
import type { TradesTab } from "../lib/trades";
import { TradesTabView } from "./Trades";
import { TradeChart } from "./trades/TradeChart";

const NB = " ";
const status: apiModule.Status = { mode: "propose", halted: false, halted_by: null, halt_reason: null, pending: 0, supervisor: {},
  supervisor_halt: false, supervisor_reasons: [], drift_halt: false, drift_reasons: [], blackout: null };

function renderTab(tab: TradesTab, route: Partial<Route> = {}, go = vi.fn()) {
  apiModule.setUser({ email: "a@x.io", role: "viewer", signed_in_with: "password_totp" });
  const free = <K extends keyof typeof apiModule.api>(k: K, v: unknown) => {
    if (!vi.isMockFunction(apiModule.api[k])) vi.spyOn(apiModule.api, k).mockResolvedValue(v as never);
  };
  free("status", status); free("accounts", []); free("proposals", []); free("recentProposals", []);
  free("openPositions", openPositions());
  free("activity", { events: [], unreadable: [] });
  free("today", { as_of: "2026-10-27T09:00:00Z", accounts: [todayAccount()] });
  free("tradesOverview", { as_of: "2026-10-27T09:00:00Z", periods: [], unreadable: [] });
  free("limits", { generated_utc: "2026-10-27T09:00:00Z", error: null, supervisor: {}, accounts: [] });
  free("trades", tradesPage());
  free("trade", tradeRow());
  free("tradeBars", bars());
  free("strategyBoard", board());
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const r: Route = { section: "trades", tab, sub: null, query: "", ...route };
  return { go, ...render(<QueryClientProvider client={qc}><TradesTabView tab={tab} route={r} go={go} /></QueryClientProvider>) };
}
const wrap = (ui: ReactNode) => render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);

afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

describe("Trades > Now (128, 130)", () => {
  it("shows money on every open row: P&L and risk in £, R, levels, time held; grouped by account", async () => {
    renderTab("now");
    const day = await screen.findByRole("list", { name: "Open trades, Day/swing lab" });
    const row = within(day).getByRole("button", { name: /Box break \+ close 15m, long, plus £3\.40, open/ });
    expect(row.textContent).toContain("+£3.40");
    expect(row.textContent).toContain(`+0.63${NB}R`);
    expect(row.textContent).toContain("risk £5.40");
    expect(row.textContent).toContain("4,012.40 → stop 4,006.10 USD/oz");
    expect(row.textContent).toContain(`22${NB}min of 2${NB}h`);
    expect(screen.queryByText(/not known/)).toBeNull();
    const scalp = screen.getByRole("region", { name: "Open trades Scalp lab" });
    expect(scalp).toHaveTextContent("2 open · risk £3.10 · +£0.50");
    expect(within(scalp).getByText("CONTROL")).toBeInTheDocument();             // kind in a word, not a colour
    expect(screen.getByRole("group", { name: "Open now summary" })).toHaveTextContent("Open now · 3 trades · risk £8.50 · P&L now +£3.90");
  });

  it("an unknown rate is a dash on the row and '(no rate)' once on the group, never 'not known'", async () => {
    const v = openPositions();
    v.positions = v.positions.map((p) => ({ ...p, floating_pnl: null }));
    vi.spyOn(apiModule.api, "openPositions").mockResolvedValue(v as never);
    renderTab("now");
    const scalp = await screen.findByRole("region", { name: "Open trades Scalp lab" });
    expect(within(scalp).getAllByText("(no rate)")).toHaveLength(1);
    expect(screen.queryByText(/not known/)).toBeNull();
    expect(screen.getByRole("group", { name: "Open now summary" })).toHaveTextContent(`P&L now £ —`);
  });

  it("filters by account and opens a trade", async () => {
    const { go } = renderTab("now");
    fireEvent.change(await screen.findByRole("combobox", { name: "Account" }), { target: { value: "icm-probe" } });
    expect(screen.queryByRole("region", { name: "Open trades Day/swing lab" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Random control \(scalp\) 1m, short/ }));
    expect(go).toHaveBeenCalledWith("trades", "now", "control:icm-probe:2", "", { keepScroll: true });
  });

  it("empty, error: words that say trading goes on", async () => {
    vi.spyOn(apiModule.api, "openPositions").mockResolvedValue({ as_of: "x", unreadable: [], positions: [] });
    const { unmount } = renderTab("now");
    expect(await screen.findByText("No open trades.")).toBeInTheDocument();
    unmount();
    vi.restoreAllMocks();
    vi.spyOn(apiModule.api, "openPositions").mockRejectedValue(new Error("down"));
    renderTab("now");
    expect(await screen.findByText("Couldn't load open trades.")).toBeInTheDocument();
    expect(screen.getByText(/Open trades keep their stop, target and automatic exits/)).toBeInTheDocument();
    expect(screen.getByRole("tablist", { name: "Decisions" })).toBeInTheDocument();   // the decisions stay
  });
});

describe("Trades > List (130)", () => {
  it("one row per trade with strategy, net after costs, R, exit reason, kind and why; the server's totals", async () => {
    renderTab("list");
    const list = await screen.findByRole("list", { name: "Trades" });
    const row = within(list).getByRole("button", { name: "Box break + close 15m, long, plus £2.10, closed at target" });
    expect(row.textContent).toContain("+£2.10");
    expect(row.textContent).toContain(`+1.50${NB}R`);
    expect(row.textContent).toContain("LONG · Day/swing lab · target");
    expect(row.textContent).toContain("Close above the 15m box after a squeeze");
    expect(within(list).getByText("CONTROL")).toBeInTheDocument();
    expect(within(list).getByText("COST PROBE")).toBeInTheDocument();
    expect(screen.getByText("412 trades · net +£18.40 after costs · costs £22.10")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Load 50 more" })).toBeInTheDocument();
  });

  it("sends the URL's filters to the API and writes chip changes back to the URL", async () => {
    const { go } = renderTab("list", { query: "acct=icm-probe&fam=box_break_close" });
    await screen.findByRole("list", { name: "Trades" });
    expect(apiModule.api.trades).toHaveBeenCalledWith(expect.stringMatching(/acct=icm-probe.*fam=box_break_close/));
    fireEvent.click(await screen.findByRole("button", { name: "Remove filter Scalp lab" }));
    expect(go).toHaveBeenCalledWith("trades", "list", null, "fam=box_break_close", { replace: true, keepScroll: true });
  });

  it("a partly-read family's filter is blinded: no net, no R, and the reason in words (ADR 0050)", async () => {
    vi.spyOn(apiModule.api, "trades").mockResolvedValue(blindedPage());
    renderTab("list", { query: "fam=opening_range" });
    expect(await screen.findByText("64 trades · costs £3.20")).toBeInTheDocument();
    expect(screen.getByText(/Results hidden: this view holds 1 unread arm \(fewer than 3\)/)).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Trades" });
    expect(list).toHaveTextContent("hidden");
    expect(list).not.toHaveTextContent(/[+−]£/);
    expect(list).not.toHaveTextContent(/\d R/);
  });

  it("falls back to the activity feed while the server has no ledger (404), and says so", async () => {
    vi.spyOn(apiModule.api, "trades").mockRejectedValue(new apiModule.ApiError(404, "Not Found"));
    renderTab("list");
    expect(await screen.findByText(/doesn't serve the trade list yet/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Activity feed" })).toBeInTheDocument();
  });

  it("no match: says so and offers to clear the filters", async () => {
    vi.spyOn(apiModule.api, "trades").mockResolvedValue(tradesPage({ rows: [], next_cursor: null,
      totals: { n: 0, net_pnl: 0, costs: 0, currency: "GBP", blinded: false, unread_arms: 0, excluded: [] } }));
    const { go } = renderTab("list", { query: "tf=4h" });
    fireEvent.click(await screen.findByRole("button", { name: "Clear filters" }));
    expect(go).toHaveBeenCalledWith("trades", "list", null, "", { replace: true, keepScroll: true });
  });
});

describe("Trade detail (133)", () => {
  it("explains one trade: chart levels in words and prices, why, closed by, costs that add up, evidence, ids under Details", async () => {
    renderTab("list", { sub: "exploration:icm-demo:41" });
    const d = await screen.findByRole("dialog", { name: "Trade detail" });
    expect(await within(d).findByRole("img", { name: /target 4,024\.00, entry 4,012\.40, first stop 4,006\.10, last stop 4,012\.40, exit 4,024\.00/ })).toBeInTheDocument();
    expect(d).toHaveTextContent("Close above the 15m box after a squeeze");
    expect(d).toHaveTextContent(`target, after 32 min (hold limit 2 h) at 4,024.00 USD/oz`);
    expect(d).toHaveTextContent(`spread £0.18 · slippage £0.02 · commission £0.07 · swap £0.00 = £0.27 (0.19 R)`);
    expect(d).toHaveTextContent("counted toward the read (213 of 300)");
    expect(within(d).getByText("Details")).toBeInTheDocument();
  });

  it("a trade without bars shows the missing-bars state, not an empty chart", async () => {
    vi.spyOn(apiModule.api, "tradeBars").mockRejectedValue(new apiModule.ApiError(404, "Not Found"));
    renderTab("list", { sub: "exploration:icm-demo:41" });
    expect(await screen.findByText("Bars for this period aren't stored yet.")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /Chart of/ })).toBeNull();
  });

  it("a blinded arm's single trade still shows its own result", async () => {
    vi.spyOn(apiModule.api, "trade").mockResolvedValue(tradeRow({ blinded: true }));
    renderTab("list", { sub: "x" });
    const d = await screen.findByRole("dialog", { name: "Trade detail" });
    expect(await within(d).findByText("+£2.10")).toBeInTheDocument();
    expect(d).toHaveTextContent(/totals stay hidden until its read/);
  });

  it("Escape and Close return to the list, keeping the filters", async () => {
    const { go } = renderTab("list", { sub: "x", query: "fam=box_break_close" });
    fireEvent.click(await screen.findByRole("button", { name: "Close trade detail" }));
    expect(go).toHaveBeenCalledWith("trades", "list", null, "fam=box_break_close", { keepScroll: true });
  });

  it("the chart puts the entry and exit markers on the right bars", () => {
    const b = bars();
    const t = tradeRow({ opened_utc: b.bars[10].t, exit_utc: b.bars[20].t });
    const { container } = wrap(<TradeChart trade={t} bars={b.bars} loading={false} height={200} />);
    expect(container.querySelectorAll("rect.up, rect.down")).toHaveLength(b.bars.length);
    expect(container.querySelector("path.marker.entry")).not.toBeNull();
    expect(container.querySelector("circle.marker.exit")).not.toBeNull();
    expect(container.textContent).toContain("target 4,024.00");
  });
});

describe("Trades > Strategies (132, ADR 0050)", () => {
  it("unread arms show progress only; a read arm its verdict with CI and vs control; a partly-read family both", async () => {
    renderTab("strategies");
    const board = await screen.findByRole("region", { name: "Strategy board" });
    const box = within(board).getByRole("button", { name: /Box break \+ close, running/ });
    expect(box).toHaveTextContent("213/300 · read ~Thu 5 Nov");
    expect(box).toHaveTextContent("retired at its stop");
    expect(box).not.toHaveTextContent(/R\/trade|near/);
    const or = within(board).getByRole("button", { name: /Opening range, READ · proven/ });
    expect(or).toHaveTextContent(`+0.21 R/trade (90% CI +0.05…+0.37 R)`);
    expect(or).toHaveTextContent(`vs control +0.24 R`);
    expect(or).toHaveTextContent(`since read: +0.18 R, n 64`);
    expect(or).toHaveTextContent("120/300");                                    // its unread 1h arm: progress only
    expect(or.textContent!.match(/R\/trade/g)).toHaveLength(1);
  });

  it("orders by state, never by result; lab money only with 3+ arms; learning line on top", async () => {
    renderTab("strategies");
    const board = await screen.findByRole("region", { name: "Strategy board" });
    const names = within(board).getAllByRole("button").map((b) => b.getAttribute("aria-label")!.split(",")[0]);
    expect(names).toEqual(["Opening range", "Box break + close", "Random control (scalp)"]);
    const top = screen.getByRole("region", { name: "Learning progress" });
    expect(top).toHaveTextContent("4 arms read, 1 proven, 3 retired, 44 running · 2,960 trades counted toward reads this week");
    expect(top).toHaveTextContent("Scalp lab (scalp), 7 days: 2,960 trades · −£41.20 net · costs £66.10 · within plan");
    expect(top).toHaveTextContent("Vantage lab (intraday), 7 days: 310 trades · net hidden (fewer than 3 arms)");
  });

  it("tapping a family opens the List filtered to it", async () => {
    const { go } = renderTab("strategies");
    fireEvent.click(await screen.findByRole("button", { name: /Box break \+ close, running/ }));
    expect(go).toHaveBeenCalledWith("trades", "list", null, "fam=box_break_close");
  });

  it("engine strategies show full figures with n and a 90% CI", async () => {
    renderTab("strategies");
    const eng = await screen.findByRole("region", { name: "Engine strategies" });
    expect(eng).toHaveTextContent(`Box break + MFI · 1h`);
    expect(eng).toHaveTextContent(`90% CI −0.08…+0.32 R`);
  });
});

describe("Trades > Today (131)", () => {
  it("per account: fills against the target with pace, net after costs by kind, open risk, caps, strip, week and month", async () => {
    renderTab("today");
    const c = await screen.findByRole("region", { name: "Today Scalp lab" });
    expect(c).toHaveTextContent("Fills 148 of 200 · pace ~240 by day end");
    expect(within(c).getByRole("meter", { name: "Fills today against the target of 200" })).toBeInTheDocument();
    expect(c).toHaveTextContent("Closed today: −£3.10 net after costs from 133 trades (costs £9.80)");
    expect(c).toHaveTextContent("Exploration −£4.20 (120 fills) · Control +£0.90 (20 fills) · Cost probe +£0.20 (8 fills)");
    expect(c).toHaveTextContent("Open: 15 trades · risk £18.20 · P&L now +£2.10");
    expect(c).toHaveTextContent("Caps: day £30.00 of £500.00 used · week £61.00 of £1,000.00 · exploration pot £30.00 of £150.00");
    expect(c.querySelectorAll(".day-strip rect")).toHaveLength(14);
    expect(c.querySelector(".day-strip line.target")).not.toBeNull();
    expect(c).toHaveTextContent("Week to date+£12.40 after costs (spread £40.10 · commission £11.20 · swap −£0.40)");
    expect(c).not.toHaveTextContent("Quiet day");
  });

  it("quiet-day wording only when the volatility screen says so; real money first", async () => {
    vi.spyOn(apiModule.api, "today").mockResolvedValue({ as_of: "x", accounts: [
      todayAccount({ quiet_day: true }), todayAccount({ account: "icm-live", account_label: "Live (ICM)", mode: "live", fills_target: null }),
    ] } as never);
    renderTab("today");
    const cards = await screen.findAllByRole("region", { name: /^Today / });
    expect(cards[0]).toHaveAccessibleName("Today Live (ICM)");
    expect(cards[0]).toHaveTextContent("REAL");
    expect(cards[1]).toHaveTextContent("Quiet day: fewer trades is expected.");
  });

  it("an older server (no new fields) shows today's money without inventing zeros", async () => {
    vi.spyOn(apiModule.api, "today").mockResolvedValue({ as_of: "x", accounts: [{
      account: "icm-demo", mode: "demo", currency: "GBP", day_pnl: 4, open_pnl: 1, closed_pnl: 3, closed_n: 2, closed_complete: true,
      costs: null, loss_meter: null, age_s: 1, stale: false }] } as never);
    renderTab("today");
    const c = await screen.findByRole("region", { name: "Today icm-demo" });
    expect(c).toHaveTextContent("+£4.00");
    expect(c).not.toHaveTextContent(/Fills|Caps|Week to date/);
    await waitFor(() => expect(screen.getByRole("region", { name: "Risk per trade" })).toBeInTheDocument());
  });
});
