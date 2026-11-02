import { describe, expect, it } from "vitest";
import { parseRoute, SECTIONS } from "./route";

// Owner 2026-11-02: the Research and Feeds sections are retired; System health is its own section.
describe("routes after the research and feeds screens were retired", () => {
  it("has five sections: Home, Trades, Limits, Health and Settings", () => {
    expect([...SECTIONS]).toEqual(["home", "trades", "limits", "health", "settings"]);
  });

  it("opens Health from #/health", () => {
    expect(parseRoute("#/health")).toEqual({ section: "health", tab: null, sub: null, query: "" });
  });

  it.each(["#/feeds", "#/feeds/health", "#/feeds/news", "#/feeds/data"])(
    "sends an old Feeds bookmark (%s) to Health", (hash) => {
      expect(parseRoute(hash)).toEqual({ section: "health", tab: null, sub: null, query: "" });
    },
  );

  it.each(["#/research", "#/research/road", "#/research/strategies"])(
    "sends an old Research bookmark (%s) to Home", (hash) => {
      expect(parseRoute(hash)).toEqual({ section: "home", tab: null, sub: null, query: "" });
    },
  );
});
