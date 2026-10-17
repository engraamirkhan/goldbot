import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import type { AttributionView, GatesView, ResultCell, StopRuleView, TradesSummary } from "../lib/api";
import { Progress, ProgressLine } from "./Progress";

// docs/ux/progress-screens.md: three read-only sections; every verdict, flag and status comes from the API.
const item = (name: string, met: boolean, over: Partial<GatesView["gates"][number]["items"][number]> = {}) => ({
  name, met, required: true, need: "", evidence: "", proposed: false, ci95_basis: "per-trade iid, return", ...over,
});

const STOP_OK: StopRuleView = {
  status: "ok", months: 4.2, after_months: 18, pooled_trades: 61, min_pooled_trades: 500, confidence: 0.9,
  lower_bound: -0.00041, min_trades_for_estimate: 30, worst_loss_frac: 0.0082, weekly_cap: 0.05,
  clock_start_utc: "2026-10-01T09:00:00Z", clock_source: "paper_gate", breach: [], incident_count: 0, incidents: [],
  imputed: { n: 2, charged_usd: 0.84, unpriced: 0 },
};

const GATES: GatesView = {
  generated_utc: "2026-10-17T13:02:00Z", phase: 2, proposed_count: 6, cant_check: null,
  proposed: [{ name: "paper_min_days", value: 182 }, { name: "live_min_days", value: 365 }],
  stop_rule: STOP_OK,
  gates: [
    { gate: "foundation_to_backtest", met: true, recorded: true, recorded_utc: "2026-10-02T10:00:00Z", met_on_proposed: false,
      items: [item("shuffle_auc", true)] },
    { gate: "backtest_to_paper", met: true, recorded: true, recorded_utc: "2026-10-09T10:00:00Z", met_on_proposed: false,
      items: [item("previous_gate", true)] },
    { gate: "paper_to_tiny_live", met: false, recorded: false, met_on_proposed: false, items: [
      item("previous_gate", true, { evidence: "recorded" }),
      item("paper_trades", false, { evidence: "12 closed demo trades", need: ">= 150", progress: { value: 12, target: 150, unit: "trades" } }),
      item("paper_days", false, { proposed: true, need: ">= 182 days", progress: { value: 7, target: 182, unit: "days" },
        earliest_utc: "2027-04-01T09:00:00Z" }),
      item("paper_expectancy", false, { evidence: "paper -0.0002 vs backtest 0.0005 per trade", ci95: [-0.0007, 0.0003] }),
      item("fills", true, { evidence: "measured slippage $0.031/oz on 41 fills vs modelled $0.150" }),
      item("chaos_drill", false, { evidence: "not recorded (run.py gate-evidence)" }),
      item("no_incident", true),
      item("shadow_record", true, { required: false, evidence: "312 closed shadow trades, mean 0.0004" }),
    ] },
    { gate: "tiny_live_to_full_size", met: false, recorded: false, met_on_proposed: false,
      items: [item("previous_gate", false), item("live_trades", false)] },
  ],
};

const cell = (over: Partial<ResultCell> = {}): ResultCell => ({
  n: 120, mean_r_gross: 0.09, mean_r_net: 0.02, std_r_net: 1, t_stat: 0.4, ci95_net: [-0.05, 0.09], hit_rate: 0.48,
  profit_factor: 1.05, verdict: "indistinguishable from zero", t_bar: 2.9, ci_adj: [-0.08, 0.12],
  verdict_adj: "indistinguishable from zero", se: 0.03, ...over,
});
const fixed = (n: number) => cell({ n, verdict: "by construction", verdict_adj: "by construction", ci_adj: null, ci95_net: null, t_stat: null, se: null });

const ATTR: AttributionView = {
  stale: false, next_run_utc: "2026-10-17T23:50:00Z", cant_check: null,
  report: {
    as_of: "2026-10-16T23:50:00Z", window_from: "2026-04-19T23:50:00Z", min_trades: 30, t_bar: 2.9, m: 3, n_candidates: 400,
    n_taken: 212,
    overall: cell({ n: 212, mean_r_net: 0.04, mean_r_gross: 0.11, ci95_net: [-0.05, 0.13] }),
    challengers: cell({ n: 0 }),
    breakdowns: {
      timeframe: { "4h": cell({ n: 21, mean_r_net: -0.1, verdict_adj: "noise" }), "15m": cell({ n: 120 }), "1h": cell({ n: 71, mean_r_net: 0.07 }) },
      exit: { stop: fixed(80), target: fixed(90), time: cell({ n: 42 }) },
    },
    exits: {},
    costs: { source: "icm-demo", n: 212, mean_r: { spread: 0.04, slippage: 0.01, commission: 0.02, swap: 0, total: 0.07 },
      share_of_gross: 0.64, killed: 18, by_session: {} },
    live: { "icm-demo": { fills: 41, mean_slippage_usd: 0.153, prior_usd: 0.15, mean_excess_usd: 0.003, t_stat: 0.3, mean_excess_r: 0.01,
      n_with_risk: 41, verdict: "indistinguishable from zero", t_bar: 2, verdict_adj: "indistinguishable from zero", orders: {} } },
    notes: ["costs from icm-demo"],
  },
};

const TRADES: TradesSummary[] = [{
  account: "icm-demo-…67", mode: "demo", currency: "GBP", days: 30, first_day: "2026-09-18", last_day: "2026-10-17",
  net: 42.1, n: 9, net_n: 9, days_traded: 3, trading_days: 22, target: 50, days_at_target: 0,
  best_day: { day: "2026-10-13", net: 31.4, n: 3 }, worst_day: { day: "2026-10-09", net: -12.8, n: 2 },
  by_day: [], gross_rows: 0, other_currency_rows: 0,
}];

function renderProgress(role: apiModule.Role = "owner", props: Parameters<typeof Progress>[0] = {}) {
  apiModule.setUser({ email: "o@x.io", role });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { qc, ...render(<QueryClientProvider client={qc}><Progress {...props} /></QueryClientProvider>) };
}

function mock(gates: GatesView | Error | "pending" = GATES, attr: AttributionView | Error | "pending" = ATTR,
              trades: TradesSummary[] | Error = TRADES) {
  const as = <T,>(v: T | Error | "pending") =>
    v === "pending" ? () => new Promise<T>(() => {}) : v instanceof Error ? () => Promise.reject(v) : () => Promise.resolve(v);
  vi.spyOn(apiModule.api, "gates").mockImplementation(as(gates));
  vi.spyOn(apiModule.api, "attribution").mockImplementation(as(attr));
  vi.spyOn(apiModule.api, "tradesSummary").mockImplementation(as(trades));
}

const section = (name: string | RegExp) => screen.getByRole("region", { name });

describe("Progress", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); });

  it("holds each section's place with a loading placeholder", () => {
    mock("pending", "pending", []);
    renderProgress();
    expect(screen.getByLabelText("Loading stop rule…")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByLabelText("Loading gates…")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByLabelText("Loading results…")).toHaveAttribute("aria-busy", "true");
    expect(screen.getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["#stop-rule", "#gates", "#results"]);
  });

  it("shows a viewer every section, without the owner's record command", async () => {
    mock({ ...GATES, gates: [{ ...GATES.gates[0], recorded: false, recorded_utc: null }, ...GATES.gates.slice(1)] });
    renderProgress("viewer");
    expect(await screen.findByRole("heading", { name: "Stop rule" })).toBeInTheDocument();
    expect(await screen.findByText("The owner records it on the server.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Road to live" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /Results/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy" })).not.toBeInTheDocument();
  });

  it("shows a met gate to the owner with the record commands and the separate live lock", async () => {
    mock({ ...GATES, gates: GATES.gates.map((g) => g.gate === "paper_to_tiny_live" ? { ...g, met: true } : g) });
    renderProgress("owner");
    const gate = await screen.findByTestId("gate-paper_to_tiny_live");
    expect(within(gate).getByText("Met", { exact: true })).toBeInTheDocument();
    expect(gate).toHaveTextContent("Met on the evidence. Recording it is your decision.");
    expect(within(gate).getByText("python -m goldbot.ops.run record-gate paper_to_tiny_live --evidence state/gate_report.json")).toBeInTheDocument();
    expect(gate).toHaveTextContent("does not turn on live");
  });

  it("opens the next gate with each item as have X of Y, the Proposed tag, the earliest date and the per-trade interval", async () => {
    mock();
    renderProgress();
    const road = await screen.findByTestId("gate-paper_to_tiny_live");
    expect(road).toHaveAttribute("open");
    expect(road).toHaveTextContent("Not met");
    expect(road).toHaveTextContent("3 of 7 met");
    expect(within(road).getByRole("meter", { name: "Demo trades, 12 of 150" })).toHaveAttribute("aria-valuenow", "12");
    expect(within(road).getByText("Proposed")).toBeInTheDocument();
    expect(road).toHaveTextContent(/earliest Thu 1 Apr 2027/);
    expect(road).toHaveTextContent("−0.07% to +0.03% per trade (95%, per-trade interval, not day-clustered)");
    expect(within(road).getByText(/For context/)).toHaveTextContent("312 closed shadow trades");
    expect(screen.getByLabelText("Demo trades: not met")).toBeInTheDocument();
    expect(screen.getByLabelText("Fills vs model: met")).toBeInTheDocument();
    expect(screen.getByTestId("gate-tiny_live_to_full_size")).toHaveTextContent("Waits for gate 3");
    expect(screen.getByTestId("gate-tiny_live_to_full_size")).not.toHaveAttribute("open");
    expect(screen.getByTestId("gate-foundation_to_backtest")).toHaveTextContent("Recorded");
    expect(section("Road to live")).toHaveTextContent("6 thresholds are proposed, not signed off.");
    expect(section("Road to live")).toHaveTextContent("Now: paper (demo). Next: gate 3, Paper → tiny live");
  });

  it("shows a gate met on a proposed threshold as that, never as a plain met", async () => {
    mock({ ...GATES, gates: GATES.gates.map((g) => g.gate === "paper_to_tiny_live" ? { ...g, met: true, met_on_proposed: true } : g) });
    renderProgress("owner");
    const gate = await screen.findByTestId("gate-paper_to_tiny_live");
    expect(gate).toHaveTextContent("Met on a proposed threshold");
    expect(within(gate).queryByText("Met", { exact: true })).not.toBeInTheDocument();
    expect(gate).toHaveTextContent("gates.signed_off");
  });

  it("marks every gate unverified when part of the evidence can't be read", async () => {
    mock({ ...GATES, cant_check: "The gate record (phase_state.json) is unreadable, so the gates are shown as if none is recorded." });
    renderProgress();
    expect(await screen.findByText(/Gates: can't check/)).toBeInTheDocument();
    expect(screen.getByTestId("gate-paper_to_tiny_live")).toHaveTextContent("(unverified)");
  });

  it("shows the stop rule not breached with both conditions and the clock", async () => {
    mock();
    renderProgress();
    const stop = await screen.findByTestId("stop-rule");
    expect(stop).toHaveTextContent("Not breached");
    expect(stop).toHaveTextContent("Should the project stop? Not on the evidence so far.");
    expect(within(stop).getByRole("meter", { name: "Time, 4.2 of 18 months" })).toBeInTheDocument();
    expect(stop).toHaveTextContent("can trigger from month 18");
    expect(stop).toHaveTextContent("Cautious estimate −0.04% per trade (lower 90% bound)");
    expect(stop).toHaveTextContent("2 trades had missing costs and were charged an estimate ($0.84 in total)");
    expect(stop).toHaveTextContent("0.82% of equity · limit 5.0%");
    expect(stop).toHaveTextContent("This check never halts trading by itself.");
    expect(within(stop).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reads too few trades to estimate below the API's minimum", async () => {
    mock({ ...GATES, stop_rule: { ...STOP_OK, pooled_trades: 12, lower_bound: null } });
    renderProgress();
    expect(await screen.findByTestId("stop-rule")).toHaveTextContent(/Edge\s*too few trades to estimate yet/);
  });

  it("shows a breach in amber with the reasons, what was not done and a way to the halt", async () => {
    const onOpenOverview = vi.fn();
    mock({ ...GATES, stop_rule: { ...STOP_OK, status: "breached", breach: ["single_loss"], worst_loss_frac: 0.056, incident_count: 3,
      incidents: [{ account: "icm-demo-…67", mode: "demo", closed_utc: "2026-10-13T15:20:00Z", loss_frac: 0.056 }] } });
    renderProgress("approver", { onOpenOverview });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Breached");
    expect(alert).toHaveTextContent("The design says the project stops.");
    expect(alert).toHaveTextContent(/One trade lost 5.6% of equity, more than the 5.0% weekly limit · icm-demo-…67 · demo/);
    expect(alert).toHaveTextContent("(+2 more)");
    expect(alert).toHaveTextContent("Nothing was halted automatically.");
    expect(alert.className).not.toMatch(/bad/);
    await userEvent.click(within(alert).getByRole("button", { name: "Go to Halt" }));
    expect(onOpenOverview).toHaveBeenCalled();
  });

  it("tells a viewer the owner decides on a breach", async () => {
    mock({ ...GATES, stop_rule: { ...STOP_OK, status: "breached", breach: ["long_run"], months: 18.3, pooled_trades: 512, lower_bound: -0.00012 } });
    renderProgress("viewer");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("After 18.3 months and 512 trades");
    expect(alert).toHaveTextContent("The owner decides whether to halt.");
    expect(within(alert).queryByRole("button")).not.toBeInTheDocument();
  });

  it("says the stop rule can't be checked, with the API's words", async () => {
    mock({ ...GATES, stop_rule: { ...STOP_OK, status: "cant_check", cant_check: "The closed-trade record is partly unreadable. Nothing is paused by this; exits always run." } });
    renderProgress();
    const stop = await screen.findByTestId("stop-rule");
    expect(stop).toHaveTextContent("Can't check");
    expect(stop).toHaveTextContent("Stop rule: can't check");
    expect(stop).toHaveTextContent("Nothing is paused by this; exits always run.");
  });

  it("says the stop rule starts at paper trading before any trade", async () => {
    mock({ ...GATES, stop_rule: { ...STOP_OK, status: "not_started", months: null, pooled_trades: 0, clock_start_utc: null, clock_source: null, imputed: null } });
    renderProgress();
    const stop = await screen.findByTestId("stop-rule");
    expect(stop).toHaveTextContent("Not started");
    expect(stop).toHaveTextContent("The stop rule starts counting at paper trading.");
  });

  it("shows results with the verdict in words, fixed row order and no range on by-construction exit rows", async () => {
    mock();
    renderProgress();
    const res = await screen.findByTestId("results");
    expect(res).toHaveTextContent("+0.04 R per trade after costs · 212 trades");
    expect(res).toHaveTextContent("Can't tell from luck");
    expect(res).toHaveTextContent("Average per trade: −0.05 to +0.13 R (95%)");
    expect(res).toHaveTextContent("Simulated trades");
    const rows = within(res).getAllByTestId("result-row");
    expect(rows.map((r) => r.dataset.key)).toEqual(["15m", "1h", "4h"]);
    expect(rows[2]).toHaveTextContent("Too few trades");
    expect(rows[2]).toHaveTextContent("21 of 30 needed");
    expect(rows[0]).toHaveTextContent("−0.08 to +0.12 R (95%, adjusted for 3 rows)");
    expect(rows[0]).toHaveTextContent("smallest average this row can tell from luck: ±0.09 R");

    await userEvent.selectOptions(within(res).getByLabelText("Break down by"), "exit");
    const exits = within(res).getAllByTestId("result-row");
    expect(exits.map((r) => r.dataset.key)).toEqual(["target", "stop", "time"]);
    expect(exits[0]).toHaveTextContent("By construction");
    expect(exits[0]).not.toHaveTextContent("Average per trade");
    expect(exits[0]).not.toHaveTextContent("tell from luck");
    expect(exits[2]).toHaveTextContent("Average per trade");

    expect(res).toHaveTextContent("Spread −0.04 R · Slippage −0.01 R · Commission −0.02 R · Swap 0.00 R");
    expect(res).toHaveTextContent("Before costs +0.11 R · costs −0.07 R per trade");
    expect(res).toHaveTextContent("Costs take 64% of the result before costs.");
    expect(res).toHaveTextContent("18 trades won before costs and lost after them.");
    expect(res).toHaveTextContent("icm-demo: 41 fills");
    expect(res).toHaveTextContent("|t| ≥ 2.9");
  });

  it("says stale results are old and when the next run is, keeping the figures", async () => {
    mock(GATES, { ...ATTR, stale: true });
    renderProgress();
    const note = await screen.findByText(/the daily results job hasn't run since/);
    expect(note).toHaveTextContent(/Next run/);
    expect(note.querySelectorAll("time")[1]).toHaveAttribute("title", "2026-10-17 23:50 UTC");
    expect(screen.getByTestId("results")).toHaveTextContent("+0.04 R per trade after costs");
  });

  it("shows results that can't be checked in amber", async () => {
    mock(GATES, { report: null, stale: false, next_run_utc: "2026-10-17T23:50:00Z",
      cant_check: "The daily results file is unreadable, so results can't be shown. Trading is not affected." });
    renderProgress();
    expect(await screen.findByText("Results: can't check")).toBeInTheDocument();
    expect(screen.getByTestId("results")).toHaveTextContent("Trading is not affected.");
  });

  it("says when the first results arrive before any report exists", async () => {
    mock(GATES, { report: null, stale: false, next_run_utc: "2026-10-17T23:50:00Z", cant_check: null });
    renderProgress();
    const empty = await screen.findByText(/No results yet/);
    expect(empty.querySelector("time")).toHaveAttribute("title", "2026-10-17 23:50 UTC");
  });

  it("hides costs and fills when no simulated trade closed in the window", async () => {
    mock(GATES, { ...ATTR, report: { ...ATTR.report!, overall: cell({ n: 0, mean_r_net: null, ci95_net: null }) } });
    renderProgress();
    const res = await screen.findByTestId("results");
    expect(res).toHaveTextContent(/No closed simulated trades in the last \d+ days yet/);
    expect(res).not.toHaveTextContent("Where costs go");
  });

  it("says an older server does not report results", async () => {
    mock(GATES, new Error("Not Found"));
    renderProgress();
    expect(await screen.findByText("Not reported by this server version.")).toBeInTheDocument();
  });

  it("shows your account's trades over 30 calendar days, labelled with the trading days", async () => {
    mock();
    renderProgress();
    const yours = await screen.findByTestId("your-trades");
    expect(yours).toHaveTextContent("last 30 calendar days (22 trading days)");
    expect(yours).toHaveTextContent("icm-demo-…67 · demo");
    expect(yours).toHaveTextContent("+£42.10 over 9 closed trades · 3 days traded");
    expect(yours).toHaveTextContent("Days with £50 or more: 0 of 22 trading days");
  });

  it("says when an account closed no trades", async () => {
    mock(GATES, ATTR, [{ ...TRADES[0], n: 0, net_n: 0, net: 0, days_traded: 0, best_day: null, worst_day: null, days_at_target: 0 }]);
    renderProgress();
    expect(await screen.findByText("No closed trades on icm-demo-…67 in the last 30 days.")).toBeInTheDocument();
  });

  it("hides the need when the item already says have X of Y", async () => {
    mock();
    renderProgress();
    const road = await screen.findByTestId("gate-paper_to_tiny_live");
    expect(road).toHaveTextContent("have 12 of 150 trades");
    expect(road).not.toHaveTextContent("needs >= 150");
    expect(road).not.toHaveTextContent("needs >= 182 days");
  });

  it("still shows the tab when a newer server sends an unknown stop-rule status or verdict", async () => {
    const report = { ...ATTR.report!, breakdowns: { timeframe: { "15m": cell({ verdict_adj: "lucky" as ResultCell["verdict_adj"] }) } } };
    mock({ ...GATES, stop_rule: { ...STOP_OK, status: "paused" as StopRuleView["status"] } }, { ...ATTR, report });
    renderProgress();
    expect(await screen.findByTestId("stop-rule")).toHaveTextContent("Unknown (paused)");
    expect(await screen.findByTestId("result-row")).toHaveTextContent("Unknown (lucky)");
    expect(screen.getByRole("heading", { name: "Road to live" })).toBeInTheDocument();
  });

  it("offers the by-agent breakdown last, on a wide screen only", async () => {
    const report = { ...ATTR.report!, breakdowns: { ...ATTR.report!.breakdowns, agent: { "tsmom-g0": cell({ n: 40 }) } } };
    mock(GATES, { ...ATTR, report });
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q === "(min-width: 960px)", media: q }));
    try {
      renderProgress();
      const select = await screen.findByLabelText("Break down by");
      const options = within(select).getAllByRole("option").map((o) => o.getAttribute("value"));
      expect(options).toEqual(["timeframe", "exit", "agent"]);
      await userEvent.selectOptions(select, "agent");
      expect(screen.getAllByTestId("result-row").map((r) => r.dataset.key)).toEqual(["tsmom-g0"]);
    } finally { vi.unstubAllGlobals(); }
  });

  it("keeps the select and the rows together when a refresh drops the chosen breakdown", async () => {
    mock();
    const { qc } = renderProgress();
    const select = await screen.findByLabelText("Break down by");
    await userEvent.selectOptions(select, "exit");
    act(() => qc.setQueryData(["attribution"], { ...ATTR, report: { ...ATTR.report!, breakdowns: { timeframe: ATTR.report!.breakdowns.timeframe } } }));
    await waitFor(() => expect(screen.getByLabelText("Break down by")).toHaveValue("timeframe"));
    expect(screen.getAllByTestId("result-row").map((r) => r.dataset.key)).toEqual(["15m", "1h", "4h"]);
  });

  it("never shows an email or a long digit run", async () => {
    mock();
    const { container } = renderProgress("owner");
    await screen.findByTestId("your-trades");
    await screen.findByTestId("results");
    expect(container.innerHTML).not.toContain("@");
    // per text node: adjacent elements' digits ("mean 0.0004" then gate "4") are not one number
    const walk = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) expect(n.textContent).not.toMatch(/\d{5,}/);
  });
});

describe("ProgressLine on Overview", () => {
  afterEach(() => vi.restoreAllMocks());

  const renderLine = (gates: GatesView, onOpen = vi.fn()) => {
    vi.spyOn(apiModule.api, "gates").mockResolvedValue(gates);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><ProgressLine onOpen={onOpen} /></QueryClientProvider>);
    return onOpen;
  };

  it("sums up the stop rule and the next gate in one line that opens Progress", async () => {
    const onOpen = renderLine(GATES);
    const line = await screen.findByRole("button", { name: /Stop rule: not breached · Next gate: Paper → tiny live, 3 of 7 met/ });
    await userEvent.click(line);
    expect(onOpen).toHaveBeenCalled();
  });

  it("raises an amber banner while the stop rule is breached", async () => {
    renderLine({ ...GATES, stop_rule: { ...STOP_OK, status: "breached", breach: ["long_run"] } });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Stop rule breached: the design says the project stops.");
    expect(within(alert).getByRole("button", { name: "Open Progress" })).toBeInTheDocument();
  });
});
