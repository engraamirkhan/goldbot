import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import { Mt5Chart } from "./Mt5Chart";

describe("MT5 chart panel", () => {
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

  it("revokes the blob URL on unmount and shows the caption", async () => {
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    vi.spyOn(apiModule.api, "mt5Chart").mockResolvedValue({ url: "blob:one", capturedAt: "2026-10-18T10:45:00Z" });
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
      .mockResolvedValueOnce({ url: "blob:a", capturedAt: null })
      .mockResolvedValue({ url: "blob:b", capturedAt: null });
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
});
