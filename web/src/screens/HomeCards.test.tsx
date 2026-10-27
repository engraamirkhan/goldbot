import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { LearningCard } from "./HomeCards";
import { OpenNow } from "./trades/Now";

const NBSP = " ";
const wrap = (ui: React.ReactElement) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);
const stats = (avg_r: number, n_r: number) => ({
  open: 0, closed: n_r, wins: 0, losses: 0, win_rate: null, n_r, total_r: avg_r * n_r, avg_r,
  net_pnl: {}, best: null, worst: null, exits: { target: 0, stop: 0, time: 0, other: 0 },
});

// Backlog 95: a number and its unit (or its count) never wrap apart on a 360 px phone. jest-dom normalises
// whitespace, so these read the raw textContent.
describe("Home cards keep number-unit pairs together", () => {
  afterEach(() => vi.restoreAllMocks());

  it("joins R and the count n to their numbers in the Learning card", async () => {
    vi.spyOn(apiModule.api, "tradesOverview").mockResolvedValue({ as_of: "2026-10-19T10:00:00Z", unreadable: [], periods: [
      { period: "7d", since: null, types: [
        { type: "shadow_all", stats: stats(0.56, 8), by_family: {}, by_timeframe: {} },
        { type: "shadow_taken", stats: stats(0.56, 8), by_family: {}, by_timeframe: {} }] }] } as never);
    wrap(<LearningCard />);
    const line = (await screen.findByText(/every candidate/)).textContent ?? "";
    expect(line).toContain(`+0.56${NBSP}R`);
    expect(line).toContain(`(n${NBSP}8)`);
    expect(line).not.toMatch(/\(n 8\)|\d R\b/);
  });

  it("joins R to its number in an open trade row, and an older engine's row (no risk yet) shows a dash, not 'not known'", async () => {
    vi.spyOn(apiModule.api, "openPositions").mockResolvedValue({ as_of: "2026-10-19T10:00:00Z", unreadable: [], positions: [
      { account: "icm-demo", mode: "demo", probe: false, position_id: 1, agent_id: "a", family: "box_break_close", timeframe: "1h",
        side: "long", lots: 0.05, entry: 2400, stop: 2396, target: 2410, opened_utc: "2026-10-19T09:00:00Z", price: 2401,
        floating_pnl: 1, currency: "GBP", r_now: 0.25, age_s: 2, stale: false, account_label: "Day/swing lab", family_label: "", kind: "strategy" }] });
    wrap(<OpenNow />);
    const row = (await screen.findByRole("listitem")).textContent ?? "";
    expect(row).toContain(`+0.25${NBSP}R`);
    expect(row).toContain(`risk £${NBSP}—`);
    expect(row).not.toContain("not known");
  });
});
