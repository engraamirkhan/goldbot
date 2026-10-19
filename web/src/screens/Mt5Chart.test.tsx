import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { Mt5Chart } from "./Mt5Chart";

describe("MT5 chart panel", () => {
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

  it("revokes the blob URL on unmount and shows the caption", async () => {
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    vi.spyOn(apiModule.api, "mt5Chart").mockResolvedValue({ url: "blob:one", capturedAt: "2026-10-18T10:45:00Z", cropped: true, symbol: "XAUUSD", isGold: true });
    const { unmount } = render(<Mt5Chart tf="15" />);
    expect(await screen.findByRole("img")).toHaveAttribute("src", "blob:one");
    expect(screen.getByText(/MT5 chart, IC Markets/)).toBeInTheDocument();
    unmount();
    expect(revoke).toHaveBeenCalledWith("blob:one");
  });

  it("revokes the old blob URL when the 60 s refresh brings a new one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const spy = vi.spyOn(apiModule.api, "mt5Chart")
      .mockResolvedValueOnce({ url: "blob:a", capturedAt: null, cropped: true, symbol: "unknown", isGold: null })
      .mockResolvedValue({ url: "blob:b", capturedAt: null, cropped: true, symbol: "unknown", isGold: null });
    render(<Mt5Chart tf="15" />);
    await screen.findByRole("img");
    await vi.advanceTimersByTimeAsync(60000);
    expect(spy).toHaveBeenCalledTimes(2);
    expect(revoke).toHaveBeenCalledWith("blob:a");
  });

  it("says in plain English when there is no fresh capture", async () => {
    vi.spyOn(apiModule.api, "mt5Chart").mockResolvedValue(null);
    render(<Mt5Chart tf="60" />);
    expect(await screen.findByText(/has not updated for over 5 minutes/)).toBeInTheDocument();
  });

  it("links to the full image, shows 24-hour update time and a stale badge after 3 minutes", async () => {
    vi.spyOn(apiModule.api, "mt5Chart").mockResolvedValue({ url: "blob:s", cropped: true, symbol: "XAUUSD", isGold: true, capturedAt: new Date(Date.now() - 4 * 60000).toISOString() });
    render(<Mt5Chart tf="15" />);
    expect(await screen.findByRole("link", { name: /full size/ })).toHaveAttribute("href", "blob:s");
    expect(screen.getByText(/updated \d\d:\d\d/)).toBeInTheDocument();
    expect(screen.getByText(/STALE/)).toBeInTheDocument();
  });

  it("shows no stale badge for a fresh capture", async () => {
    vi.spyOn(apiModule.api, "mt5Chart").mockResolvedValue({ url: "blob:f", cropped: true, symbol: "XAUUSD", isGold: true, capturedAt: new Date(Date.now() - 30000).toISOString() });
    render(<Mt5Chart tf="15" />);
    await screen.findByRole("img");
    expect(screen.queryByText(/STALE/)).toBeNull();
  });

  it("notes when MT5 is showing a symbol other than gold", async () => {
    vi.spyOn(apiModule.api, "mt5Chart").mockResolvedValue({ url: "blob:x", capturedAt: null, cropped: true, symbol: "EURUSD", isGold: false });
    render(<Mt5Chart tf="15" />);
    expect(await screen.findByText("MT5 is showing EURUSD — gold chart not open")).toBeInTheDocument();
  });

  it("shows no note when gold is open or the title could not be read", async () => {
    vi.spyOn(apiModule.api, "mt5Chart").mockResolvedValue({ url: "blob:x", capturedAt: null, cropped: true, symbol: "unknown", isGold: null });
    render(<Mt5Chart tf="15" />);
    await screen.findByRole("img");
    expect(screen.queryByText(/gold chart not open/)).toBeNull();
  });
});
