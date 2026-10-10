import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import type { HealthView } from "../lib/explain";
import { ddState, psiBand } from "../lib/explain";
import { Health } from "./Health";

function renderHealth() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><Health /></QueryClientProvider>);
}

const agent = {
  agent_id: "tsmom-g0", version: "tsmom-v3", size_factor: 0.5, halted: false, halted_since: null, halt_reasons: [],
  notes: ["PSI > 0.25 on atr_14: sized down"], psi_warn: ["adx_14"], psi_size_down: ["atr_14"], n_live_rows: 80, ece: 0.05,
  brier: 0.2, n_calib: 40, cusum: 1.2, cusum_alarm: false, dd_30d: 0.04, backtest_dd: 0.05, capital_weight: 0.6,
};

const EMPTY: HealthView = {
  generated_utc: "2026-10-10T08:00:00Z", drift_ts: null, drift_error: null, system_halt: null, agents: [],
  psi: { features: [], agents: [], values: [], warn: 0.1, size_down: 0.25 }, reliability: [], cusum: [], dd_mult: 1.5,
  checks: [{ name: "deploy", status: "ok", reason: "no deploy recorded yet" },
    { name: "data_quality", status: "ok", reason: "no data-quality events recorded" },
    { name: "drift", status: "ok", reason: "no drift report yet" }],
  errors: {},
};

const FULL: HealthView = {
  ...EMPTY,
  drift_ts: "2026-10-09T06:00:00Z",
  system_halt: { since: "2026-10-09T06:00:00Z", reasons: ["trend-g1: 30-day drawdown 9.0% > 1.5x backtest 5.0%"],
    review_command: "python -m goldbot.ops.run drift-review",
    clear_command: 'python -m goldbot.ops.run drift-review --clear "what you checked"' },
  agents: [agent, { ...agent, agent_id: "trend-g1", version: "trend-v1", size_factor: 1, halted: true,
    halted_since: "2026-10-08T06:00:00Z", halt_reasons: ["CUSUM alarm on trade residuals: agent halted"], notes: [],
    dd_30d: 0.09, capital_weight: null }],
  psi: { features: ["atr_14", "adx_14"], agents: ["trend-g1", "tsmom-g0"], values: [[0.05, 0.31], [null, 0.12]], warn: 0.1, size_down: 0.25 },
  reliability: [{ agent_id: "tsmom-g0", version: "tsmom-v3", n: 12, ece: 0.15, brier: 0.1,
    bins: [{ lo: 0.3, hi: 0.4, n: 6, mean_p: 0.35, hit_rate: 0 }, { lo: 0.7, hi: 0.8, n: 6, mean_p: 0.7, hit_rate: 1 }] }],
  cusum: [{ agent_id: "trend-g1", version: "trend-v1", k: 0.5, h: 4, alarm: true,
    points: [{ ts: "2026-10-01T10:00:00Z", z: -2, s: 1.5 }, { ts: "2026-10-02T10:00:00Z", z: -3.5, s: 4.5 }] }],
  checks: [{ name: "deploy", status: "warn", reason: "rolled_back abcdef12 on vps" }, ...EMPTY.checks.slice(1)],
};

describe("Health", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows a loading state, then the empty states before the VPS runs", async () => {
    vi.spyOn(apiModule.api, "health").mockResolvedValue(EMPTY);
    renderHealth();
    expect(screen.getByRole("status")).toHaveTextContent("Loading health");
    expect(await screen.findByText(/drift watch has not run yet/)).toBeInTheDocument();
    expect(screen.getByText(/No drift check yet/)).toBeInTheDocument();
    expect(screen.getByText(/No PSI yet/)).toBeInTheDocument();
    expect(screen.getByText(/No closed shadow trades yet. The curve/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Health checks" })).getAllByRole("listitem")).toHaveLength(3);
  });

  it("shows the API error in place of the screen", async () => {
    vi.spyOn(apiModule.api, "health").mockRejectedValue(new Error("boom"));
    renderHealth();
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load health (boom)");
  });

  it("shows the system halt with the exact review command, agents, PSI bands, charts and checks", async () => {
    vi.spyOn(apiModule.api, "health").mockResolvedValue(FULL);
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    renderHealth();
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("System halt");
    expect(banner).toHaveTextContent("30-day drawdown 9.0%");
    expect(within(banner).getByText('python -m goldbot.ops.run drift-review --clear "what you checked"')).toBeInTheDocument();
    await userEvent.click(within(banner).getAllByRole("button", { name: "Copy" })[1]);
    expect(write).toHaveBeenCalledWith('python -m goldbot.ops.run drift-review --clear "what you checked"');

    const rows = screen.getAllByRole("row").filter((r) => r.closest("table")?.classList.contains("agents-health"));
    expect(rows[1]).toHaveTextContent("50%: sized down");
    expect(rows[1]).toHaveTextContent("60%");
    expect(rows[2]).toHaveTextContent(/Halted since/);
    expect(rows[2]).toHaveTextContent("CUSUM alarm on trade residuals");

    const hot = screen.getByTitle(/tsmom-g0 · atr_14: PSI 0.310 \(sized down\)/);
    expect(hot).toHaveClass("psi", "bad");
    expect(screen.getByTitle(/tsmom-g0 · adx_14: PSI 0.120 \(drifting\)/)).toHaveClass("warn");
    expect(screen.getByTitle("trend-g1 does not use adx_14")).toHaveTextContent("·");

    expect(screen.getByRole("img", { name: /Reliability of tsmom-g0/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /CUSUM of trend-g1 over 2 trades: now 4.50, halts above 4/ })).toBeInTheDocument();
    expect(screen.getByText("alarm: halted")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "trend-g1 30-day drawdown" })).toHaveAttribute("aria-valuetext", "9.0% of a 7.5% limit");
    expect(screen.getByText("9.0% of 7.5% limit: system halt")).toBeInTheDocument();
    expect(screen.getByText("warning")).toBeInTheDocument();
  });
});

describe("explain helpers", () => {
  it("bands PSI at the warn and size-down thresholds", () => {
    expect([0.05, 0.1, 0.11, 0.25, 0.26].map((v) => psiBand(v, 0.1, 0.25))).toEqual(["ok", "ok", "warn", "warn", "bad"]);
  });
  it("compares the 30-day drawdown with 1.5x backtest", () => {
    expect(ddState(0.04, 0.05, 1.5).band).toBe("ok");
    expect(ddState(0.06, 0.05, 1.5).band).toBe("warn");
    const over = ddState(0.08, 0.05, 1.5);
    expect(over.band).toBe("bad");
    expect(over.limit).toBeCloseTo(0.075);
    expect(ddState(0.5, null, 1.5)).toEqual({ limit: null, band: "ok" });
  });
});
