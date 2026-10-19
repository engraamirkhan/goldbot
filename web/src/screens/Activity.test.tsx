import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { eventLine } from "../lib/activity";
import { ActivityFeed } from "./Activity";

type Ev = apiModule.ActivityEvent;
const ev = (over: Partial<Ev>): Ev => ({ ts: "2026-10-19T10:00:00Z", kind: "refused", ...over } as Ev);

function renderFeed() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><ActivityFeed /></QueryClientProvider>);
}

describe("Activity feed", () => {
  afterEach(() => vi.restoreAllMocks());

  it("lists events newest first as the API sends them, each with its kind", async () => {
    vi.spyOn(apiModule.api, "activity").mockResolvedValue({ unreadable: [], events: [
      ev({ ts: "2026-10-19T10:05:00Z", kind: "refused", agent_id: "cost_probe:mfi-g0", reason: "cost_probe_refused:probe_loss_unknown", account: "icm-demo-…67" }),
      ev({ ts: "2026-10-19T10:00:00Z", kind: "shadow_closed", agent_id: "box_break_close-g0", timeframe: "1h", side: "long", entry: 2400, exit: 2406, reason: "target", r: 1.5 }),
      ev({ ts: "2026-10-19T09:00:00Z", kind: "position_opened", agent_id: "tsmom-g0", timeframe: "4h", side: "short", lots: 0.03, entry: 2401.5, account: "icm-demo-…67", mode: "demo" }),
    ] });
    renderFeed();
    const list = await screen.findByRole("list", { name: "Activity" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("Refused");
    expect(rows[0]).toHaveTextContent("cost_probe_refused:probe_loss_unknown");
    expect(rows[1]).toHaveTextContent("Shadow closed");
    expect(rows[1]).toHaveTextContent("+1.50 R");
    expect(rows[2]).toHaveTextContent("Demo opened");
  });

  it("asks for the newest 50 events and asks again every 15 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const spy = vi.spyOn(apiModule.api, "activity").mockResolvedValue({ unreadable: [], events: [] });
      renderFeed();
      await screen.findByText("Nothing yet");
      expect(spy).toHaveBeenCalledTimes(1);
      expect(spy).toHaveBeenCalledWith(50);
      await vi.advanceTimersByTimeAsync(15_000);
      expect(spy).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("says which source could not be read and still shows the rest", async () => {
    vi.spyOn(apiModule.api, "activity").mockResolvedValue({ unreadable: ["shadow"], events: [
      ev({ kind: "probe_closed", agent_id: "mfi-g0", side: "long", entry: 2400.4, exit: 2398.4, reason: "stop", pnl: -2, currency: "GBP", r: -0.5 }),
    ] });
    renderFeed();
    expect(await screen.findByText(/Couldn't read: shadow trades/)).toBeInTheDocument();
    expect(screen.getByRole("listitem")).toHaveTextContent("−£2.00");
  });

  it("shows an error that says trading is not affected", async () => {
    vi.spyOn(apiModule.api, "activity").mockRejectedValue(new Error("down"));
    renderFeed();
    expect(await screen.findByRole("alert")).toHaveTextContent("Trading is not affected");
  });
});

describe("eventLine", () => {
  it("writes a closed trade as agent, timeframe, side, prices, reason, R and money", () => {
    expect(eventLine(ev({ kind: "position_closed", agent_id: "mfi-g0", timeframe: "15m", side: "short", lots: 0.05,
                          entry: 2410, exit: 2405.2, reason: "target", r: 1.4, pnl: 12.5, currency: "GBP", account: "icm-demo-…67" })))
      .toBe("mfi-g0 · 15m · short 0.05 lots · 2410.00 → 2405.20 · target · +1.40 R · +£12.50 · icm-demo-…67");
  });

  it("shows an open trade's stop and take-profit target", () => {
    expect(eventLine(ev({ kind: "shadow_opened", agent_id: "box_break_close-g0", timeframe: "15m", side: "short",
                          entry: 4172.91, stop: 4184.6, target: 4149.53 })))
      .toBe("box_break_close-g0 · 15m · short · at 4172.91 · SL 4184.60 · TP 4149.53");
  });

  it("writes a refusal as agent, code and account", () => {
    expect(eventLine(ev({ kind: "refused", agent_id: "tsmom-g0", reason: "gate:market_closed", account: "icm-demo-…67" })))
      .toBe("tsmom-g0 · gate:market_closed · icm-demo-…67");
  });
});
