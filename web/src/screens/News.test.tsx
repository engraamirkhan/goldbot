import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { News } from "./News";

const iso = (minutesFromNow: number) => new Date(Date.now() + minutesFromNow * 60_000).toISOString();

const event = (over: Partial<apiModule.CalendarEvent>): apiModule.CalendarEvent => ({
  event_id: "ism", ts_utc: iso(60), country: "USD", title: "ISM Manufacturing PMI", impact: "High", tier: 2,
  forecast: "49.5", previous: "48.7", blackout_start: null, blackout_end: null, ...over,
});

const headline = (over: Partial<apiModule.Headline>): apiModule.Headline => ({
  item_id: "n1", ts_utc: iso(-10), received_utc: iso(-9), source: "forexlive", title: "Fed's Powell signals pause",
  link: "https://example.com/n1", relevance: 0.8, rates: "dovish", risk: "risk_on", dollar: "negative", surprise: "none",
  shock: false, ...over,
});

function renderNews() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><News /></QueryClientProvider>);
}

describe("News", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the calendar with tiers and blackout windows, and the active blackout", async () => {
    vi.spyOn(apiModule.api, "calendar").mockResolvedValue({
      now: iso(0), note: null,
      events: [
        event({}),
        event({ event_id: "cpi", title: "CPI m/m", tier: 1, ts_utc: iso(10), forecast: "0.3%", previous: "",
                blackout_start: iso(-5), blackout_end: iso(40) }),
      ],
      active_blackout: { kind: "calendar", title: "CPI m/m", ts_utc: iso(10), received_utc: null, accounts: ["icm-demo", "vantage-demo"] },
    });
    vi.spyOn(apiModule.api, "news").mockResolvedValue([]);
    renderNews();
    expect(await screen.findByRole("alert")).toHaveTextContent(/Entry blackout active.*CPI m\/m.*icm-demo, vantage-demo/);
    const cpi = screen.getAllByText("CPI m/m").map((el) => el.closest("tr")).find(Boolean)!;
    expect(cpi).toHaveClass("bad");                                  // inside its window now
    expect(within(cpi).getByText("T1")).toHaveClass("tier1");
    expect(within(cpi).getByText(/now/)).toBeInTheDocument();
    expect(within(cpi).getByText("—")).toBeInTheDocument();           // empty previous
    const ism = screen.getByText("ISM Manufacturing PMI").closest("tr")!;
    expect(ism).not.toHaveClass("bad");
    expect(within(ism).getByText("T2")).toBeInTheDocument();
    expect(within(ism).getByText("49.5")).toBeInTheDocument();
    expect(screen.getByText(/No headlines collected/)).toBeInTheDocument();
  });

  it("lists headlines with relevance and direction tags, highlights shocks and marks unscored items", async () => {
    vi.spyOn(apiModule.api, "calendar").mockResolvedValue({ now: iso(0), events: [], active_blackout: null, note: "no calendar archived for this window" });
    const news = vi.spyOn(apiModule.api, "news").mockResolvedValue([
      headline({}),
      headline({ item_id: "n2", title: "Missile strike near Gulf shipping lane", relevance: 0.95, rates: "neutral", risk: "risk_off",
                 dollar: "positive", shock: true, link: null }),
      headline({ item_id: "n3", title: "Weekend football results", relevance: null, rates: null, risk: null, dollar: null,
                 surprise: null, link: null }),
    ]);
    renderNews();
    expect(await screen.findByText(/no calendar archived/)).toBeInTheDocument();
    const fed = (await screen.findByRole("link", { name: "Fed's Powell signals pause" })).closest("tr")!;
    expect(within(fed).getByText("0.80")).toBeInTheDocument();
    expect(within(fed).getByText("dovish")).toBeInTheDocument();
    expect(within(fed).getByText("USD−")).toBeInTheDocument();
    expect(within(fed).queryByText("none")).toBeNull();
    const shock = screen.getByText("Missile strike near Gulf shipping lane").closest("tr")!;
    expect(shock).toHaveClass("shock");
    expect(within(shock).getByText("SHOCK")).toBeInTheDocument();
    expect(within(shock).queryByText("neutral")).toBeNull();
    expect(within(screen.getByText("Weekend football results").closest("tr")!).getByText("unscored")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Minimum relevance"), "0.7");
    expect(news).toHaveBeenLastCalledWith({ hours: 24, min_relevance: 0.7 });
  });
});
