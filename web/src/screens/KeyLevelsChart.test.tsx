import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import type { KeyLevelsView } from "../lib/api";
import { KeyLevelsChart } from "./KeyLevelsChart";

const zone = (price: number, pct: number | null, tfs: string[], role: "support" | "resistance") =>
  ({ price, lo: price - 1, hi: price + 1, strength: 5, strength_pct: pct, tfs, touches: 2, role, flipped: false });

const bars = Array.from({ length: 10 }, (_, i) => ({ t: new Date(Date.UTC(2026, 9, 27, i)).toISOString(), o: 2400 + i, h: 2403 + i, l: 2398 + i, c: 2401 + i }));

const VIEW: KeyLevelsView = {
  timeframe: "1h", account_id: "icm-demo", feed: "broker", bars, symbol: "XAUUSD",
  accounts: [{
    account_id: "icm-demo", as_of: "2026-10-27T10:00:00Z", price: 2410, atr_1d: 30, history_days: 800,
    zones_total: 36, pct_ready: true, stale: false, symbol: "XAUUSD",
    above: [zone(2420, 92, ["1h", "1d"], "resistance"), zone(2440, 30, ["round"], "resistance")],
    below: [zone(2395, 70, ["15m", "4h"], "support")],
  }, {
    account_id: "vantage-demo", as_of: "2026-10-27T10:00:00Z", price: 2411, atr_1d: 30, history_days: 20,
    pct_ready: false, stale: false, above: [], below: [], symbol: "XAUUSD",
  }],
};

function wrap(children: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("key-level chart", () => {
  afterEach(() => vi.restoreAllMocks());

  it("draws each zone as a band, thicker when stronger, labelled with its timeframes", async () => {
    vi.spyOn(apiModule.api, "keyLevels").mockResolvedValue(VIEW);
    const { container } = render(wrap(<KeyLevelsChart tf="1h" />));
    expect(await screen.findByText("Resistance 2420.00 · 1h 1d · strength 92%")).toBeInTheDocument();
    expect(screen.getByText("Support 2395.00 · 15m 4h · strength 70%")).toBeInTheDocument();
    const bands = [...container.querySelectorAll("g.zone rect")];
    expect(bands).toHaveLength(3);
    const width = (p: number) => Number(container.querySelector(`g.zone[data-price="${p}"] rect`)?.getAttribute("stroke-width"));
    expect(width(2420)).toBeGreaterThan(width(2440));
    expect(container.querySelectorAll("g.candle")).toHaveLength(10);
    expect(screen.getByRole("img", { name: /Key levels on 1h bars/ })).toBeInTheDocument();
  });

  it("switches account and asks the server for its bars", async () => {
    const spy = vi.spyOn(apiModule.api, "keyLevels").mockResolvedValue(VIEW);
    render(wrap(<KeyLevelsChart tf="4h" />));
    const pick = await screen.findByRole("combobox", { name: "Account" });
    fireEvent.change(pick, { target: { value: "vantage-demo" } });
    expect(spy).toHaveBeenLastCalledWith("4h", "vantage-demo", "XAUUSD");
  });

  it("picks another instrument, asks for its levels and labels them in its digits", async () => {
    vi.spyOn(apiModule.api, "instruments").mockResolvedValue([
      { symbol: "XAUUSD", label: "Gold", account: "icm-probe", group: "gold", digits: 2, unit: "USD/oz", quote_ccy: "USD", state: "can_enter" },
      { symbol: "EURUSD", label: "Euro / dollar", account: "icm-probe", group: "fx", digits: 5, unit: "", quote_ccy: "USD", state: "can_enter" },
    ]);
    const fx = { ...VIEW, symbol: "EURUSD", accounts: [{ ...VIEW.accounts[0], symbol: "EURUSD", price: 1.0845,
      above: [zone(1.08512, 80, ["1h"], "resistance")], below: [] }] };
    const spy = vi.spyOn(apiModule.api, "keyLevels").mockResolvedValue(fx);
    const onSymbol = vi.fn();
    render(wrap(<KeyLevelsChart tf="1h" symbol="EURUSD" onSymbol={onSymbol} />));
    expect(await screen.findByText("Resistance 1.08512 · 1h · strength 80%")).toBeInTheDocument();
    expect(spy).toHaveBeenLastCalledWith("1h", undefined, "EURUSD");
    const pick = await screen.findByRole("combobox", { name: "Instrument" });
    expect(within(pick).getByRole("group", { name: "icm-probe · FX" })).toBeInTheDocument();
    fireEvent.change(pick, { target: { value: "XAUUSD" } });
    expect(onSymbol).toHaveBeenCalledWith("XAUUSD");
  });

  it("says no levels yet for an instrument the engine has not built", async () => {
    vi.spyOn(apiModule.api, "keyLevels").mockResolvedValue({ ...VIEW, symbol: "US500", accounts: [], bars: [], account_id: null, feed: null });
    render(wrap(<KeyLevelsChart tf="1h" symbol="US500" />));
    expect(await screen.findByText(/No levels yet for S&P 500 \(US500\)/)).toBeInTheDocument();
  });

  it("says in words when no engine has published zones yet", async () => {
    vi.spyOn(apiModule.api, "keyLevels").mockResolvedValue({ ...VIEW, accounts: [], bars: [], account_id: null, feed: null });
    render(wrap(<KeyLevelsChart tf="15m" />));
    expect(await screen.findByText(/No key levels yet/)).toBeInTheDocument();
  });

  it("says how much history the zones were built on and how many of all zones it shows", async () => {
    vi.spyOn(apiModule.api, "keyLevels").mockResolvedValue(VIEW);
    render(wrap(<KeyLevelsChart tf="1h" />));
    expect(await screen.findByText(/Built on 2\.2 years of history/)).toBeInTheDocument();
    expect(screen.getByText(/Showing the 3 strongest nearby of 36 zones/)).toBeInTheDocument();
    expect(screen.queryByText(/short of 540 days/)).toBeNull();
    expect(screen.queryByText(/STALE/)).toBeNull();
  });

  it("warns when the history is short and badges zones over 30 minutes old", async () => {
    const acc = { ...VIEW.accounts[0], history_days: 62, stale: true };
    vi.spyOn(apiModule.api, "keyLevels").mockResolvedValue({ ...VIEW, accounts: [acc] });
    render(wrap(<KeyLevelsChart tf="1h" />));
    expect(await screen.findByText(/short of 540 days/)).toBeInTheDocument();
    expect(screen.getByText(/STALE: last built/)).toBeInTheDocument();
  });
});
