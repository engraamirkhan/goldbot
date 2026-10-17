import { describe, expect, it } from "vitest";
import type { GateView, ResultCell } from "./api";
import { gateStates, headlineVerdict, intervalLabel, nextGate, orderedRows, rowLabel, signedPct, signedR } from "./progress";

const item = (name: string, met: boolean, required = true) => ({
  name, met, required, need: "", evidence: "", proposed: false, ci95_basis: "per-trade iid, return",
});
const gate = (id: string, over: Partial<GateView> = {}): GateView => ({
  gate: id, met: false, recorded: false, items: [item("x", false)], met_on_proposed: false, ...over,
});
const cell = (over: Partial<ResultCell> = {}): ResultCell => ({
  n: 150, mean_r_gross: 0.1, mean_r_net: 0.05, std_r_net: 1, t_stat: 2.5, ci95_net: [0.01, 0.09], hit_rate: 0.5,
  profit_factor: 1.2, verdict: "positive", t_bar: 3, ci_adj: [-0.01, 0.11], verdict_adj: "indistinguishable from zero",
  se: 0.02, ...over,
});

describe("gate states", () => {
  it("reads recorded, met, not met with k of n, and waits for an unrecorded previous gate", () => {
    const gates = [
      gate("foundation_to_backtest", { recorded: true, met: true }),
      gate("backtest_to_paper", { met: true }),
      gate("paper_to_tiny_live", { items: [item("previous_gate", false), item("a", true), item("c", false, false)] }),
      gate("tiny_live_to_full_size", { items: [item("previous_gate", false), item("b", true)] }),
    ];
    expect(gateStates(gates).map((s) => [s.kind, s.badge])).toEqual([
      ["recorded", "Recorded"], ["met", "Met"], ["waits", "Waits"], ["waits", "Waits"],
    ]);
    const open = gateStates([gate("foundation_to_backtest", { recorded: true }),
      gate("backtest_to_paper", { items: [item("previous_gate", true), item("a", false), item("b", false), item("c", false, false)] })]);
    expect(open[1]).toMatchObject({ kind: "not_met", badge: "Not met", line: "1 of 3 met" });
  });

  it("never reads a gate met on a proposed threshold as a plain met", () => {
    const [s] = gateStates([gate("foundation_to_backtest", { met: true, met_on_proposed: true })]);
    expect(s.kind).toBe("met_proposed");
    expect(s.badge).toBe("Met on a proposed threshold");
    expect(s.tone).toBe("info");
  });

  it("names the next gate as the first one not recorded", () => {
    const gates = [gate("foundation_to_backtest", { recorded: true }), gate("backtest_to_paper"), gate("paper_to_tiny_live")];
    expect(nextGate(gates)).toBe(1);
    expect(nextGate([gate("foundation_to_backtest", { recorded: true })])).toBeNull();
  });
});

describe("results words", () => {
  it("shows no ahead or behind on the headline below 100 trades", () => {
    expect(headlineVerdict(cell({ n: 99, verdict: "positive" }))).toBe("indistinguishable from zero");
    expect(headlineVerdict(cell({ n: 99, verdict: "negative" }))).toBe("indistinguishable from zero");
    expect(headlineVerdict(cell({ n: 99, verdict: "noise" }))).toBe("noise");
    expect(headlineVerdict(cell({ n: 100, verdict: "positive" }))).toBe("positive");
  });

  it("keeps each breakdown in its natural order, never by result, with unknown keys last", () => {
    const rows = { "1d": cell(), "15m": cell({ mean_r_net: -1 }), "4h": cell(), "1h": cell({ mean_r_net: 9 }), "2h": cell() };
    expect(orderedRows("timeframe", rows).map(([k]) => k)).toEqual(["15m", "1h", "4h", "1d", "2h"]);
    expect(orderedRows("session", { newyork: cell(), asia: cell(), london: cell() }).map(([k]) => k)).toEqual(["asia", "london", "newyork"]);
    expect(orderedRows("family", { trend: cell(), breakout: cell(), session_open: cell() }).map(([k]) => k))
      .toEqual(["breakout", "session_open", "trend"]);
  });

  it("puts plain words on the raw keys", () => {
    expect(rowLabel("regime", "low")).toBe("Calm");
    expect(rowLabel("regime", "high")).toBe("Busy");
    expect(rowLabel("exit", "policy")).toBe("Exit rule");
    expect(rowLabel("session", "newyork")).toBe("New York");
    expect(rowLabel("decision", "not_taken")).toBe("Not taken");
    expect(rowLabel("family", "session_open")).toBe("Session open");
  });

  it("formats R and returns with a true minus and a unit", () => {
    expect(signedR(0.04)).toBe("+0.04 R");
    expect(signedR(-0.1)).toBe("−0.10 R");
    expect(signedPct(-0.0002)).toBe("−0.02%");
  });

  it("labels the gates' interval as per-trade, not day-clustered", () => {
    expect(intervalLabel("per-trade iid, return")).toBe("per-trade interval, not day-clustered");
    expect(intervalLabel("something else")).toBe("something else");
  });
});
