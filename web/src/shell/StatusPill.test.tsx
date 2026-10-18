import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import type { AccountSummary, EntryBlock, Status } from "../lib/api";
import { StatusPill } from "./StatusPill";

const status = (over: Partial<Status> = {}): Status => ({
  mode: "propose", halted: false, pending: 0, supervisor: {}, supervisor_halt: false, supervisor_reasons: [], drift_halt: false,
  drift_reasons: [], blackout: null, ...over,
});
const blk = (code: string, over: Partial<EntryBlock> = {}): EntryBlock => ({
  code, text: code === "weekend" ? "Weekend: no new trades from Friday evening" : "Market closed", fix: "Nothing to do.",
  kind: "wait", scope: "account", agent_id: null, at: null, at_label: null, runbook: null, runbook_url: null, ...over,
});
const acct = (blocks: EntryBlock[]): AccountSummary => ({
  account_id: "icm-demo", broker: "icm", mode: "demo", equity: 1000, day_pnl_pct: 0, week_pnl_pct: 0, drawdown_pct: 0,
  stage: "normal", open_positions: 0, account_class: "raw",
  entry_status: { state: blocks.length ? "blocked" : "open", headline: "", blocks, notes: [], exits: "", as_of: null },
});

function renderPill(s: Status, a: AccountSummary[]) {
  vi.spyOn(apiModule.api, "status").mockResolvedValue(s);
  vi.spyOn(apiModule.api, "accounts").mockResolvedValue(a);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><StatusPill /></QueryClientProvider>);
}

describe("StatusPill", () => {
  afterEach(() => vi.restoreAllMocks());

  it("is calm grey when the only reason is the market being shut, with when it opens", async () => {
    renderPill(status(), [acct([blk("market_closed"), blk("weekend", { at: "2026-10-18T22:00:00Z", at_label: "opens" })])]);
    const pill = await screen.findByText(/^Market closed · opens /);
    expect(pill.closest("button")).toHaveClass("idle");
  });

  it("stays red when the market is shut and something else blocks too", async () => {
    renderPill(status({ halted: true }), [acct([blk("market_closed")])]);
    const pill = await screen.findByText(/^Entries blocked:/);
    expect(pill.closest("button")).toHaveClass("bad");
  });

  it("says trading normally, with the count waiting", async () => {
    renderPill(status({ pending: 1 }), [acct([])]);
    expect((await screen.findByText("Trading normally · 1 waiting")).closest("button")).toHaveClass("ok");
  });
});
