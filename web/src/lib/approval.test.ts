import { describe, expect, it } from "vitest";
import type { AccountSummary, DecidedProposal, Proposal, Status } from "./api";
import { REASONS, blockers, cardState, countdown, money, via } from "./approval";
import { featureValue, plainFeature } from "./features";

const NOW = Date.parse("2026-10-10T12:00:00Z");
const at = (s: number) => new Date(NOW + s * 1000).toISOString();
const prop = (expiresIn: number): Proposal => ({
  proposal_id: "p", account_id: "icm-demo", agent_id: "a", side: "long", lots: 0.05, entry: 1, stop: 0, target: 2, p: 0.6,
  ev_r: 0.2, spread_points: 20, top_features: [], expires_at: at(expiresIn),
});
const status = (over: Partial<Status> = {}): Status => ({
  mode: "propose", halted: false, pending: 0, supervisor: {}, supervisor_halt: false, supervisor_reasons: [], drift_halt: false,
  drift_reasons: [], blackout: null, ...over,
});
const account = (over: Partial<AccountSummary> = {}): AccountSummary => ({
  account_id: "icm-demo", broker: "icm", mode: "demo", equity: 10000, day_pnl_pct: 0, week_pnl_pct: 0, drawdown_pct: 0.01,
  stage: "normal", open_positions: 0, account_class: "raw", ...over,
});

describe("cardState", () => {
  it("is pending above 15 s, expiring at 15 s and below, expired at 0", () => {
    expect(cardState(prop(90), NOW)).toBe("pending");
    expect(cardState(prop(16), NOW)).toBe("pending");
    expect(cardState(prop(15), NOW)).toBe("expiring");
    expect(cardState(prop(1), NOW)).toBe("expiring");
    expect(cardState(prop(0), NOW)).toBe("expired");
    expect(cardState(prop(-30), NOW)).toBe("expired");
  });

  it("takes a decided proposal's status from the server whatever the clock says", () => {
    for (const s of ["submitted", "approved", "rejected", "expired", "refused"] as const) {
      const d: DecidedProposal = { ...prop(60), status: s, refusal: [] };
      expect(cardState(d, NOW)).toBe(s);
    }
  });
});

describe("formatting", () => {
  it("formats the countdown as m:ss", () => {
    expect(countdown(90)).toBe("1:30");
    expect(countdown(9)).toBe("0:09");
    expect(countdown(-3)).toBe("0:00");
  });
  it("formats money and the decision channel", () => {
    expect(money(48)).toBe("$48");
    expect(money(1234.5)).toBe("$1,235");
    expect(via("dashboard:a@x.io")).toBe("dashboard (a@x.io)");
    expect(via("telegram:111")).toBe("Telegram");
    expect(via(null)).toBe("");
  });
  it("offers exactly the backend's reason codes", () => {
    expect(REASONS.map((r) => r.code)).toEqual(["news", "cost", "discretion", "duplicate", "other"]);
  });
});

describe("blockers", () => {
  it("is empty when entries are allowed", () => {
    expect(blockers(status(), [account()], "icm-demo")).toEqual([]);
  });
  it("lists every halt that stops a new entry", () => {
    const s = status({ halted: true, supervisor_halt: true, drift_halt: true,
                       blackout: { kind: "calendar", title: "US CPI", ts_utc: at(60), received_utc: null, accounts: ["icm-demo"] } });
    expect(blockers(s, [account({ stage: "halted", account_class: "unknown" })], "icm-demo")).toEqual(
      ["owner halt", "supervisor halt", "drift halt", "news blackout", "drawdown halt", "account class unknown"]);
  });
  it("scopes blackouts and account state to the proposal's account; paper ignores the class", () => {
    const s = status({ blackout: { kind: "news_shock", title: "x", ts_utc: null, received_utc: at(0), accounts: ["other"] } });
    expect(blockers(s, [account(), account({ account_id: "other", stage: "halted" })], "icm-demo")).toEqual([]);
    expect(blockers(status(), [account({ mode: "paper", account_class: "unknown" })], "icm-demo")).toEqual([]);
    expect(blockers(s, [account({ account_id: "other", stage: "halted" })])).toEqual(["news shock", "drawdown halt (other)"]);
  });
});

describe("plainFeature", () => {
  it("names known features in plain words", () => {
    expect(plainFeature("adx14")).toBe("Trend strength (ADX 14)");
    expect(plainFeature("atr_14")).toBe("Volatility (ATR 14)");
    expect(plainFeature("rsi14_extreme")).toBe("Overbought/oversold (RSI 14)");
    expect(plainFeature("donchian_pos_20")).toBe("Position in 20-bar high-low range");
    expect(plainFeature("h1_rsi14")).toBe("Momentum (RSI 14), 1h chart");
  });
  it("humanises unknown names and formats values compactly", () => {
    expect(plainFeature("weird_new_thing")).toBe("Weird new thing");
    expect(featureValue(31.234)).toBe("31.2");
    expect(featureValue(-0.2)).toBe("-0.20");
    expect(featureValue(1234.4)).toBe("1234");
  });
});
