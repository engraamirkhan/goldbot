import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import type { LimitAccount, LimitChange, LimitsView } from "../lib/limits";
import { Limits, LimitsStrip } from "./Limits";

const NOW = new Date().toISOString();

export const change = (over: Partial<LimitChange> = {}): LimitChange => ({
  account_id: "icm-demo", ts: "2026-10-17T08:12:00Z", field: "daily_cap", old_value: 0.04, new_value: 0.05,
  unit: "fraction_of_equity", source: "static", reason: "The owner's approved demo ceilings apply (ADR 0032).",
  adr: "0032", ...over,
});

export const account = (over: Partial<LimitAccount> = {}): LimitAccount => ({
  account_id: "icm-demo", mode: "demo", profile: "demo-2026-10-18", source: "static", adr: "0032", updated_utc: NOW,
  stale: false, currency: "GBP", balance: 1000, day_start_equity: 1000, week_start_equity: 1200, daily_cap_pct: 0.05,
  daily_limit: 50, daily_used: 12, daily_resets_utc: "2026-10-19T00:00:00Z", absolute_daily_limit: 50,
  weekly_cap_pct: 0.1, weekly_limit: 120, weekly_used: 12, weekly_resets_utc: "2026-10-25T00:00:00Z", min_lot: 0.01,
  min_lot_risk: 8, min_lot_risk_cap: 0.01, revert_active: false, revert_since: null, revert_reason: null,
  cap_hit: "none", last_change: change(), error: null, ...over,
});

const view = (accounts: LimitAccount[]): LimitsView => ({
  generated_utc: NOW, accounts, error: null,
  supervisor: { daily_cap: 0.0375, weekly_cap: 0.075, default_daily: 0.015, default_weekly: 0.04, envelope_daily: 0.0375,
                envelope_weekly: 0.075 },
});

function renderIt(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

describe("Limits card", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows each cap in money and percent with what is used as a bar and a number", async () => {
    vi.spyOn(apiModule.api, "limits").mockResolvedValue(view([account()]));
    renderIt(<Limits />);
    const card = await screen.findByRole("region", { name: /Limits for icm-demo/ });
    expect(within(card).getByText("£50")).toBeInTheDocument();
    expect(within(card).getByText(/5\.0% of £1,000 at today's start/)).toBeInTheDocument();
    expect(within(card).getByText(/10\.0% of £1,200 at the week's start/)).toBeInTheDocument();
    const day = within(card).getByRole("meter", { name: "Today's loss limit used" });
    expect(day).toHaveAttribute("aria-valuetext", "£12 of £50 used, 24%");
    expect(within(card).getByText(/Profile: demo-2026-10-18/)).toBeInTheDocument();
    expect(within(card).getByText(/A 0\.01 lot trade risks £8 at the current stop/)).toBeInTheDocument();
    expect(within(card).getByText(/Auto-revert at 8%: ON/)).toBeInTheDocument();
    expect(within(card).getByText(/Daily loss limit 4\.0% -> 5\.0%/)).toBeInTheDocument();
    expect(within(card).getByText(/approved demo ceilings apply/)).toBeInTheDocument();
    expect(card.querySelector(".state.bad")).toBeNull();
  });

  it("says close to today's limit in amber at 75% used", async () => {
    vi.spyOn(apiModule.api, "limits").mockResolvedValue(view([account({ daily_used: 40 })]));
    renderIt(<Limits />);
    const chip = await screen.findByText("Close to today's limit");
    expect(chip).toHaveClass("warn");
  });

  it("says in red that entries stop and exits keep working when the daily cap is spent", async () => {
    vi.spyOn(apiModule.api, "limits").mockResolvedValue(view([account({ daily_used: 50, cap_hit: "daily" })]));
    renderIt(<Limits />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Daily loss limit reached\. New entries are stopped until 00:00 UTC/);
    expect(alert).toHaveTextContent(/Exits keep working\./);
    expect(alert).toHaveClass("bad");
  });

  it("shows an active revert as information, not as red", async () => {
    vi.spyOn(apiModule.api, "limits").mockResolvedValue(view([account({
      profile: "default", source: "revert", revert_active: true, revert_since: "2026-10-16T10:30:00Z",
      revert_reason: "The account fell 8% from its peak (size-down stage).", daily_cap_pct: 0.02, daily_limit: 20,
    })]));
    renderIt(<Limits />);
    const note = await screen.findByText(/Limits are on the default profile \(8% revert active since/);
    expect(note.closest(".info-note")).not.toBeNull();
    expect(screen.getByText(/fell 8% from its peak/)).toBeInTheDocument();
    expect(document.querySelector(".bad")).toBeNull();
  });

  it("says when no limit change is logged yet", async () => {
    vi.spyOn(apiModule.api, "limits").mockResolvedValue(view([account({ last_change: null })]));
    renderIt(<Limits />);
    expect(await screen.findByText("No limit changes yet. Limits are the profile defaults.")).toBeInTheDocument();
  });

  it("lists every change, newest first, with who made it and why", async () => {
    vi.spyOn(apiModule.api, "limits").mockResolvedValue(view([account()]));
    const list = vi.spyOn(apiModule.api, "limitChanges").mockResolvedValue([
      change({ ts: "2026-10-17T09:00:00Z", source: "revert", reason: "The account reached a drawdown stage." }),
      change(),
    ]);
    renderIt(<Limits />);
    fireEvent.click(await screen.findByRole("button", { name: "See all changes" }));
    const rows = await screen.findAllByRole("listitem");
    expect(list).toHaveBeenCalledWith({ account: "icm-demo", limit: 20 });
    expect(rows[0]).toHaveTextContent(/the safety revert/);
    expect(rows[1]).toHaveTextContent(/your approved demo ceilings/);
  });

  it("greys out an engine report that is over a minute old", async () => {
    vi.spyOn(apiModule.api, "limits").mockResolvedValue(view([account({
      stale: true, updated_utc: new Date(Date.now() - 300_000).toISOString() })]));
    renderIt(<Limits />);
    expect(await screen.findByText("Updated 5 min ago")).toHaveClass("idle");
  });

  it("names the error and says the engines fail closed, with a retry", async () => {
    vi.spyOn(apiModule.api, "limits").mockRejectedValue(new apiModule.ApiError(500, "core.db unreadable"));
    renderIt(<Limits />);
    expect(await screen.findByText(/Could not read the limits \(core\.db unreadable\)\. Engines fail closed on this; see Health\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("shows a skeleton of the card's height while loading", () => {
    vi.spyOn(apiModule.api, "limits").mockReturnValue(new Promise(() => {}));
    renderIt(<Limits />);
    expect(screen.getByLabelText("Loading limits")).toHaveAttribute("aria-busy", "true");
  });

  it("shows the combined caps of all accounts against the CFO envelope", async () => {
    vi.spyOn(apiModule.api, "limits").mockResolvedValue(view([account()]));
    renderIt(<Limits />);
    expect(await screen.findByText(/All accounts together: daily 3\.75%, weekly 7\.5%/)).toBeInTheDocument();
  });

  it("never renders an email address or a login", async () => {
    vi.spyOn(apiModule.api, "limits").mockResolvedValue(view([account()]));
    const { container } = renderIt(<Limits />);
    await screen.findByRole("region", { name: /Limits for icm-demo/ });
    expect(container.textContent).not.toMatch(/@/);
  });
});

describe("Limits strip on Overview", () => {
  afterEach(() => vi.restoreAllMocks());

  it("gives one line per account and opens the Limits tab", async () => {
    vi.spyOn(apiModule.api, "limits").mockResolvedValue(view([account()]));
    const open = vi.fn();
    renderIt(<LimitsStrip onOpen={open} />);
    const line = await screen.findByRole("button", { name: /icm-demo: today £12 of £50 used, week £12 of £120/ });
    fireEvent.click(line);
    expect(open).toHaveBeenCalled();
  });
});
