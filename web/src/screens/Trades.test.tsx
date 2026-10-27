import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import type { Route } from "../lib/route";
import { bars, board, openPositions, ranking, todayAccount, tradeBars, tradeRow, tradesPage } from "../test/tradesFixtures";
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
  free("tradeBars", tradeBars());
  free("strategyBoard", board());
  free("strategyRanking", ranking());
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
    expect(scalp).toHaveTextContent("2 open · risk £3.10 · P&L now +£2.10");    // the broker's account figure, not a sum of rows
    expect(within(scalp).getByText("CONTROL")).toBeInTheDocument();             // kind in a word, not a colour
    expect(screen.getByRole("group", { name: "Open now summary" })).toHaveTextContent("Open now · 3 trades · risk £8.50");
    expect(screen.getByRole("group", { name: "Open now summary" })).not.toHaveTextContent("P&L");   // never summed across accounts
    expect(screen.getByRole("region", { name: "Open trades Day/swing lab" })).toHaveTextContent("P&L now £ —");   // no figure from the server
  });

  it("an unknown rate is a dash on the row and '(no rate)' once on the group, never 'not known'", async () => {
    const v = openPositions();
    v.positions = v.positions.map((p) => ({ ...p, floating_pnl: null }));
    vi.spyOn(apiModule.api, "openPositions").mockResolvedValue(v as never);
    renderTab("now");
    const scalp = await screen.findByRole("region", { name: "Open trades Scalp lab" });
    expect(within(scalp).getAllByText("(no rate)")).toHaveLength(1);
    expect(screen.queryByText(/not known/)).toBeNull();
  });

  it("filters by account; an engine position opens its trade, an arm's opens the List filtered to it", async () => {
    const { go } = renderTab("now");
    fireEvent.change(await screen.findByRole("combobox", { name: "Account" }), { target: { value: "icm-probe" } });
    expect(screen.queryByRole("region", { name: "Open trades Day/swing lab" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Opening range 5m, long/ }));
    expect(go).toHaveBeenCalledWith("trades", "now", "position:icm-probe:3", "", { keepScroll: true });
    fireEvent.click(screen.getByRole("button", { name: /Random control \(scalp\) 1m, short/ }));
    expect(go).toHaveBeenCalledWith("trades", "list", null, "acct=icm-probe&fam=random_bracket&status=open");
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
    expect(screen.getByText("412 trades (3 open) · net +£18.40 after costs · costs £22.10 · open risk £8.50")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Load 50 more" })).toBeInTheDocument();
  });

  it("sends the URL's filters to the API and writes chip changes back to the URL", async () => {
    const { go } = renderTab("list", { query: "acct=icm-probe&fam=box_break_close" });
    await screen.findByRole("list", { name: "Trades" });
    expect(apiModule.api.trades).toHaveBeenCalledWith(expect.stringMatching(/account=icm-probe.*family=box_break_close/));
    fireEvent.click(await screen.findByRole("button", { name: "Remove filter Scalp lab" }));
    expect(go).toHaveBeenCalledWith("trades", "list", null, "fam=box_break_close", { replace: true, keepScroll: true });
  });

  it("a filter on one strategy shows its rows' results and its totals (ADR 0053, 139)", async () => {
    renderTab("list", { query: "fam=opening_range" });
    expect(await screen.findByText("412 trades (3 open) · net +£18.40 after costs · costs £22.10 · open risk £8.50")).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Trades" });
    expect(list).toHaveTextContent("+£2.10");
    expect(screen.queryByText(/hidden/i)).toBeNull();
  });

  it("names an unreadable source: its trades are missing, not zero", async () => {
    vi.spyOn(apiModule.api, "trades").mockResolvedValue(tradesPage({ unreadable: ["exploration"] }));
    renderTab("list");
    expect(await screen.findByText(/Couldn't read exploration trades: those trades are missing from this list and its totals, not zero/)).toBeInTheDocument();
  });

  it("an error says trading goes on and offers a retry", async () => {
    vi.spyOn(apiModule.api, "trades").mockRejectedValue(new apiModule.ApiError(500, "boom"));
    renderTab("list");
    expect(await screen.findByText("Couldn't load the trades.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("no match: says so and offers to clear the filters", async () => {
    vi.spyOn(apiModule.api, "trades").mockResolvedValue(tradesPage({ rows: [], next_cursor: null,
      totals: { ...tradesPage().totals, n: 0, n_open: 0, n_closed: 0, net_pnl: null, costs: null } }));
    const { go } = renderTab("list", { query: "tf=4h" });
    fireEvent.click(await screen.findByRole("button", { name: "Clear filters" }));
    expect(go).toHaveBeenCalledWith("trades", "list", null, "", { replace: true, keepScroll: true });
  });
});

describe("Trade detail (133)", () => {
  it("explains one trade: why, closed by, levels, costs that add up to gross minus net, evidence, ids under Details", async () => {
    renderTab("list", { sub: "exploration:icm-demo:41" });
    const d = await screen.findByRole("dialog", { name: "Trade detail" });
    expect(await within(d).findByText("Close above the 15m box after a squeeze")).toBeInTheDocument();
    expect(d).toHaveTextContent(`target, after 32 min (hold limit 2 h) at 4,024.00 USD/oz`);
    expect(d).toHaveTextContent(`spread £0.18 · slippage +£0.02 · commission £0.07 · swap £0.00 = £0.27 · gross +£2.37`);
    expect(d).toHaveTextContent("counted toward the read");
    expect(within(d).getByText("Details")).toBeInTheDocument();
    expect(apiModule.api.trade).toHaveBeenCalledWith("exploration:icm-demo:41");
  });

  it("draws the trade on its bars: entry, first and last stop, target and exit labelled; the move in words; the feed named", async () => {
    renderTab("list", { sub: "exploration:icm-demo:41" });
    const chart = await screen.findByRole("img", { name: /^Chart of 15m bars: target 4,024\.00, entry 4,012\.40, first stop 4,006\.10, last stop 4,012\.40, exit 4,024\.00/ });
    expect(chart.querySelectorAll("rect.up, rect.down")).toHaveLength(40);
    const d = screen.getByRole("dialog", { name: "Trade detail" });
    expect(d).toHaveTextContent(/stop 4,006\.10 \(moved to 4,012\.40 at/);
    expect(d).toHaveTextContent("15m bars, bid prices, from this broker.");
    expect(apiModule.api.tradeBars).toHaveBeenCalledWith("exploration:icm-demo:41");
  });

  it("no stored bars: the missing-bars state with the levels in words, not an empty chart", async () => {
    vi.spyOn(apiModule.api, "tradeBars").mockResolvedValue(tradeBars({ bars: [], feed: null, stop_moves: [], missing: "Bars for this period aren't stored yet" }));
    renderTab("list", { sub: "exploration:icm-demo:41" });
    expect(await screen.findByText("Bars for this period aren't stored yet.")).toBeInTheDocument();
    expect(screen.getByText("first stop 4,006.10 USD/oz")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /Chart of/ })).toBeNull();
  });

  it("bars from the reference feed or a coarser timeframe say so", async () => {
    vi.spyOn(apiModule.api, "tradeBars").mockResolvedValue(tradeBars({ feed: "reference", timeframe: "1h" }));
    renderTab("list", { sub: "exploration:icm-demo:41" });
    expect(await screen.findByText("1h bars (a long hold: coarser than its 15m), bid prices, from the reference feed (this broker's bars aren't stored).")).toBeInTheDocument();
  });

  it("a trade the ledger doesn't hold (404) says so", async () => {
    vi.spyOn(apiModule.api, "trade").mockRejectedValue(new apiModule.ApiError(404, "no such trade"));
    renderTab("list", { sub: "position:icm-demo:999" });
    expect(await screen.findByText("This trade isn't in the ledger.")).toBeInTheDocument();
  });

  it("an arm's single trade shows its own result and points to its family's figures", async () => {
    renderTab("list", { sub: "x" });
    const d = await screen.findByRole("dialog", { name: "Trade detail" });
    expect(await within(d).findByText("+£2.10")).toBeInTheDocument();
    expect(d).toHaveTextContent(/its family's figures, with n and the CI, are on Strategies/);
  });

  it("sorts by result from the URL and writes a new order back to it", async () => {
    const { go } = renderTab("list", { query: "sort=worst" });
    await screen.findByRole("list", { name: "Trades" });
    expect(apiModule.api.trades).toHaveBeenCalledWith(expect.stringMatching(/sort=worst/));
    fireEvent.change(screen.getByRole("combobox", { name: "Order" }), { target: { value: "best" } });
    expect(go).toHaveBeenCalledWith("trades", "list", null, "sort=best", { replace: true, keepScroll: true });
  });

  it("Escape and Close return to the list, keeping the filters", async () => {
    const { go } = renderTab("list", { sub: "x", query: "fam=box_break_close" });
    fireEvent.click(await screen.findByRole("button", { name: "Close trade detail" }));
    expect(go).toHaveBeenCalledWith("trades", "list", null, "fam=box_break_close", { keepScroll: true });
  });

  it("the chart puts the entry and exit markers on the right bars", () => {
    const b = bars();
    const t = tradeRow({ opened_utc: b[10].t, exit_utc: b[20].t });
    const { container } = wrap(<TradeChart trade={t} bars={b} loading={false} height={200} />);
    expect(container.querySelectorAll("rect.up, rect.down")).toHaveLength(b.length);
    expect(container.querySelector("path.marker.entry")).not.toBeNull();
    expect(container.querySelector("circle.marker.exit")).not.toBeNull();
    expect(container.textContent).toContain("target 4,024.00");
  });
});

describe("Trades > Strategies, ranked (136-138, ADR 0053)", () => {
  it("is ranked by default in the server's order, with the heading line, n, CI and status on every row", async () => {
    renderTab("strategies");
    const card = await screen.findByRole("region", { name: "Strategy ranking" });
    expect(await within(card).findByText(/^Ranked by the least each trade is likely earning after costs\./)).toBeInTheDocument();
    expect(apiModule.api.strategyRanking).toHaveBeenCalledWith("?window=all");
    const rows = within(card).getAllByRole("button", { name: /open its trades/ }).map((b) => b.getAttribute("aria-label")!.split(",")[0]);
    expect(rows).toEqual(["1. Opening range", "2. Box break + close", "Sweep + absorb", "Random control", "EMA pullback"]);
    const first = within(card).getByRole("button", { name: /^1\. Opening range/ });
    expect(first).toHaveTextContent(`+0.03 R/trade at least`);
    expect(first).toHaveTextContent(`avg +0.12 R (95% CI +0.05…+0.19 R) · 412 fills, 18 days`);
    expect(first).toHaveTextContent("hit 48 %");
    expect(first).toHaveTextContent("+£38.10 (+£0.09/trade)");
    expect(first).toHaveTextContent("PF 1.31");
    expect(first).toHaveTextContent(`vs random +0.10 R (interim`);
    expect(first).toHaveTextContent("early, not proven");
    expect(first).toHaveTextContent("120 of 300 to the read");    // the server's progress, on the row's own fills
    // a phone: the rows too few to rank are one compact list, a line each, with the same progress as their fills
    const few = within(within(card).getByRole("region", { name: "Too few to rank" })).getByRole("button", { name: /^Sweep \+ absorb/ });
    expect(few).toHaveTextContent("Sweep + absorb avg +0.80 R · 12 fills, 4 days · 12 of 300 to the read");
    expect(few.closest(".family-card")).toBeNull();
    expect(within(card).getByRole("region", { name: "Controls" })).toHaveTextContent("Random control");
    expect(within(card).getByRole("region", { name: "Retired" })).toHaveTextContent("retired at its stop");
  });

  it("the order toggle and the window are remembered in the URL", async () => {
    const { go } = renderTab("strategies");
    await screen.findByRole("region", { name: "Strategy ranking" });
    fireEvent.click(screen.getByRole("tab", { name: "By progress to read" }));
    expect(go).toHaveBeenCalledWith("trades", "strategies", null, "order=progress", { replace: true, keepScroll: true });
    fireEvent.click(screen.getByRole("tab", { name: "7 days" }));
    expect(go).toHaveBeenCalledWith("trades", "strategies", null, "window=7d", { replace: true, keepScroll: true });
  });

  it("a ranking that can't load says so and that trading is not affected", async () => {
    vi.spyOn(apiModule.api, "strategyRanking").mockRejectedValue(new apiModule.ApiError(500, "boom"));
    renderTab("strategies");
    expect(await screen.findByText("Couldn't load the ranking.")).toBeInTheDocument();
  });
});

describe("Trades > Strategies, by progress to read (132, ADR 0050, 0051)", () => {
  it("unread arms show progress only; a read arm its verdict with CI and vs control; a partly-read family both", async () => {
    renderTab("strategies", { query: "order=progress" });
    const board = await screen.findByRole("region", { name: "Strategy board" });
    const box = within(board).getByRole("button", { name: /Box break \+ close, running/ });
    expect(box).toHaveTextContent("213/300 · read ~Thu 5 Nov");
    expect(box).toHaveTextContent("retired at its stop");
    expect(box).toHaveTextContent("read against Random control (scalp) 1m");
    expect(box).not.toHaveTextContent(/R\/trade|near/);
    const or = within(board).getByRole("button", { name: /Opening range, READ · proven/ });
    expect(or).toHaveTextContent(`+0.21 R/trade (90% CI +0.05…+0.37 R)`);
    expect(or).toHaveTextContent(`vs control +0.24 R`);
    expect(or).toHaveTextContent(`since read: +0.18 R, n 64`);
    expect(or).toHaveTextContent("120/300");                                    // its unread 1h arm: progress only
    expect(or.textContent!.match(/R\/trade/g)).toHaveLength(1);
  });

  it("by progress keeps the server's order (state, then closeness to the read); lab money; learning line on top", async () => {
    renderTab("strategies", { query: "order=progress" });
    const board = await screen.findByRole("region", { name: "Strategy board" });
    const names = within(board).getAllByRole("button").map((b) => b.getAttribute("aria-label")!.split(",")[0]);
    expect(names).toEqual(["Opening range", "Box break + close", "Random control (scalp)"]);
    const top = screen.getByRole("region", { name: "Learning progress" });
    expect(top).toHaveTextContent("4 arms read, 1 proven, 3 retired, 44 running · 2,960 trades counted toward reads in 7 days");
    expect(top).toHaveTextContent("Scalp lab (scalp), 7 days: 2,960 trades · −£41.20 net · costs £66.10");
    expect(top).toHaveTextContent("Vantage lab (intraday), 7 days: 310 trades · net not known yet");
  });

  it("tapping a family opens the List filtered to it", async () => {
    const { go } = renderTab("strategies", { query: "order=progress" });
    fireEvent.click(await screen.findByRole("button", { name: /Box break \+ close, running/ }));
    expect(go).toHaveBeenCalledWith("trades", "list", null, "fam=box_break_close");
  });

  it("a board that can't load says so and that trading is not affected", async () => {
    vi.spyOn(apiModule.api, "strategyBoard").mockRejectedValue(new apiModule.ApiError(500, "boom"));
    renderTab("strategies", { query: "order=progress" });
    expect(await screen.findByText("Couldn't load strategies.")).toBeInTheDocument();
    expect(screen.getByText(/Trading is not affected/)).toBeInTheDocument();
  });

  it("a family read pooled shows one pooled progress and ETA; its arms only their own counts (quant 2026-10-27)", async () => {
    const v = board();
    const fam = v.families[1];
    v.families[1] = { ...fam, pooled: [{ unit_id: "H-81:Z6", arm_ids: ["KA15", "KA1H"], n_counted: 140, n_read_at: 300, status: "running",
                                         anneal_state: "E", eta_read_date: "2026-11-20", read: null }],
                      variants: fam.variants.slice(0, 2).map((x) => ({ ...x, pooled: true, read_unit: "H-81:Z6", eta_read_date: null })) };
    vi.spyOn(apiModule.api, "strategyBoard").mockResolvedValue(v);
    renderTab("strategies", { query: "order=progress" });
    const box = await screen.findByRole("button", { name: /Box break \+ close, running/ });
    expect(box).toHaveTextContent("All timeframes pooled");
    expect(box).toHaveTextContent("140/300 · read ~Fri 20 Nov");
    expect(within(box).getAllByRole("meter")).toHaveLength(1);                  // the pooled one, no per-arm meter
    expect(box).toHaveTextContent("213 counted toward the family's pooled read");
    expect(box).not.toHaveTextContent("213/300");
  });

  it("an arm without a counted trade reads warming up; twins on two labs name their lab", async () => {
    const v = board();
    v.families[1] = { ...v.families[1], accounts: ["Day/swing lab", "Vantage lab"], variants: [
      { ...v.families[1].variants[0], status: "warming", n_counted: 0, eta_read_date: null },
      { ...v.families[1].variants[0], arm_id: "VKA15", account_labels: ["Vantage lab"] },
    ] };
    vi.spyOn(apiModule.api, "strategyBoard").mockResolvedValue(v);
    renderTab("strategies", { query: "order=progress" });
    const box = await screen.findByRole("button", { name: /Box break \+ close, running/ });
    expect(box).toHaveTextContent("0/300 · warming up: no counted trade yet");
    expect(box).toHaveTextContent("Vantage lab");
  });

  it("engine strategies show full figures with n and a 90% CI", async () => {
    renderTab("strategies", { query: "order=progress" });
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
    expect(c).toHaveTextContent("Closed today: −£3.10 net after costs (costs £9.80) · open +£2.10");
    expect(c).toHaveTextContent("Exploration −£4.20 (120 fills) · Control +£0.90 (20 fills) · Cost probe +£0.20 (8 fills)");
    expect(c).toHaveTextContent("Open: 15 trades · risk £18.20 · P&L now +£2.10");
    expect(c).toHaveTextContent("Caps: day £30.00 of £500.00 used · week £61.00 of £1,000.00 · exploration pot £30.00 of £150.00");
    expect(c.querySelectorAll(".day-strip rect")).toHaveLength(14);
    expect(c.querySelector(".day-strip line.target")).not.toBeNull();
    expect(c).toHaveTextContent("Week to date+£12.40 after costs (spread £40.10 · slippage +£1.00 · commission £11.20 · swap −£0.40)");
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

  it("an unknown net is 'incomplete: N trades without costs yet', never a full total", async () => {
    const a = todayAccount({ net_today: null, net_unknown: 2, net_today_partial: -1.5,
      month_to_date: { since: "2026-10-01", n: 3000, net_pnl: null, costs: null, net_unknown: 4, net_partial: 80 } });
    vi.spyOn(apiModule.api, "today").mockResolvedValue({ as_of: "x", accounts: [a] });
    renderTab("today");
    const c = await screen.findByRole("region", { name: "Today Scalp lab" });
    expect(c).toHaveTextContent("Closed today: net incomplete: 2 trades without costs yet (known trades −£1.50)");
    expect(c).toHaveTextContent("Month to date3000 trades · net incomplete: 4 trades without costs yet (known trades +£80.00)");
  });

  it("an unreadable source is named, never zero", async () => {
    vi.spyOn(apiModule.api, "today").mockResolvedValue({ as_of: "x", accounts: [todayAccount()], unreadable: ["probes"] });
    renderTab("today");
    await screen.findByRole("region", { name: "Today Scalp lab" });
    expect(screen.getByText(/Couldn't read cost probes: those trades are missing from today's figures, not zero/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("region", { name: "Risk per trade" })).toBeInTheDocument());
  });
});
