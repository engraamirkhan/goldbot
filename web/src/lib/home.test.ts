import { describe, expect, it } from "vitest";
import type { AccountSummary, EntryBlock, FeedHealth, LossMeter, Proposal, TradeStats, TradesOverview } from "./api";
import { digest, meterWords, modeBadge, notOpen, openTradesWords, orderWaiting, shadowTiles, strategyName, systemLine } from "./home";

const gbp = (x: number) => `£${x.toFixed(2)}`;

const prop = (id: string, account: string, inSec: number): Proposal => ({
  proposal_id: id, account_id: account, agent_id: "box_break_close-g0", side: "long", lots: 0.01, entry: 2400, stop: 2396,
  target: 2408, p: 0.6, ev_r: 0.2, spread_points: 18, risk_usd: 4, min_lot_exception: false, top_features: [],
  expires_at: new Date(1_760_000_000_000 + inSec * 1000).toISOString(), created: 1_760_000_000,
});
const acct = (id: string, mode: "demo" | "live") => ({ account_id: id, mode }) as AccountSummary;

const stats = (avg_r: number | null, n_r: number): TradeStats => ({
  open: 0, closed: n_r, wins: 0, losses: 0, win_rate: null, n_r, total_r: avg_r == null ? null : avg_r * n_r, avg_r,
  net_pnl: {}, best: null, worst: null, exits: { target: 0, stop: 0, time: 0, other: 0 },
} as TradeStats);

function overview(week: Record<string, TradeStats>, all: Record<string, TradeStats>, allCands = stats(0.05, 210)): TradesOverview {
  const type = (t: string, s: TradeStats, fam: Record<string, TradeStats>) => ({ type: t, stats: s, by_family: fam, by_timeframe: {} });
  return {
    as_of: "2026-10-19T10:00:00Z", unreadable: [],
    periods: [
      { period: "7d", since: null, types: [type("shadow_taken", stats(0.27, 48), week), type("shadow_all", allCands, {})] },
      { period: "all", since: null, types: [type("shadow_taken", stats(0.1, 300), all)] },
    ],
  } as TradesOverview;
}

describe("Home card logic", () => {
  it("labels a live account REAL and everything else DEMO", () => {
    expect(modeBadge("live")).toBe("REAL");
    expect(modeBadge("demo")).toBe("DEMO");
    expect(modeBadge(null)).toBe("DEMO");
  });

  it("puts a waiting real-money proposal first even when a demo one expires sooner", () => {
    const out = orderWaiting([prop("d", "icm-demo", 10), prop("r", "icm-live", 80)], [acct("icm-demo", "demo"), acct("icm-live", "live")]);
    expect(out.map((p) => p.proposal_id)).toEqual(["r", "d"]);
  });

  it("orders proposals of the same kind by expiry", () => {
    const out = orderWaiting([prop("b", "x", 50), prop("a", "x", 20)], undefined);
    expect(out.map((p) => p.proposal_id)).toEqual(["a", "b"]);
  });

  it("names the probe stop when it binds first, and is red only when a limit is used up", () => {
    const m: LossMeter = { used: 6, binding: "probe_stop", limits: [
      { key: "probe_stop", label: "", limit: 16, remaining: 10 }, { key: "daily_limit", label: "", limit: 50, remaining: 44 }] };
    expect(meterWords(m, gbp)).toEqual({ text: "£10.00 left before the probe stop (£16.00)", tone: "ok", limit: 16 });
    const hit: LossMeter = { used: 50, binding: "daily_limit", limits: [{ key: "daily_limit", label: "", limit: 50, remaining: 0 }] };
    expect(meterWords(hit, gbp)).toMatchObject({ tone: "bad", text: "daily loss limit reached: no new entries today" });
    const near: LossMeter = { used: 13, binding: "probe_stop", limits: [{ key: "probe_stop", label: "", limit: 16, remaining: 3 }] };
    expect(meterWords(near, gbp).tone).toBe("warn");
  });

  it("ranks the digest by the largest change in R and keeps at most three lines, each with n", () => {
    const v = overview(
      { box_break_close: stats(0.31, 14), mfi: stats(-0.05, 9), tsmom: stats(0.12, 20), session_open: stats(0.5, 3) },
      { box_break_close: stats(0.12, 80), mfi: stats(0.4, 60), tsmom: stats(0.11, 200), session_open: stats(0.45, 40) },
    );
    const lines = digest(v);
    expect(lines.map((l) => l.family)).toEqual(["mfi", "box_break_close", "session_open"]);
    expect(lines[1].text).toBe("Box break close: +0.31 R per trade over 7 days (n 14), all time +0.12 R (n 80)");
  });

  it("has no digest line when the 7 days are the same as all time", () => {
    expect(digest(overview({ tsmom: stats(0.56, 8) }, { tsmom: stats(0.56, 8) }))).toEqual([]);
  });

  it("has no digest line for a family without an all-time baseline", () => {
    expect(digest(overview({ new_family: stats(0.2, 4) }, {}))).toEqual([]);
  });

  it("gives the two shadow tiles as R per trade with n", () => {
    expect(shadowTiles(overview({}, {}))).toEqual([
      { label: "All candidates, 7 days", avgR: 0.05, n: 210 }, { label: "Taken, 7 days", avgR: 0.27, n: 48 }]);
  });

  it("names a strategy in words with its timeframe", () => {
    expect(strategyName("box_break_close", "1h")).toBe("Box break close 1h");
  });
});

describe("system status line", () => {
  const NOW = Date.parse("2026-10-19T10:00:00Z");
  const block = (code: string, over: Partial<EntryBlock> = {}): EntryBlock =>
    ({ code, scope: "account", text: code, fix: "", kind: "wait", ...over });
  const a = (blocks: EntryBlock[] = [], asOf = "2026-10-19T09:59:55Z") => ({ account_id: "icm-demo", mode: "demo",
    entry_status: { state: blocks.length ? "waiting" : "open", headline: "", blocks, notes: [], exits: "", as_of: asOf } }) as unknown as AccountSummary;
  const feed = (age: number) => ({ account_id: "icm-demo", last_tick_age_s: age }) as FeedHealth;
  const words = (p: ReturnType<typeof systemLine>) => p.map((x) => `${x.label} ${x.word} (${x.tone})`);

  it("reads all well in words, never red", () => {
    expect(words(systemLine({ accounts: [a()], feeds: [feed(2)], now: NOW })))
      .toEqual(["Engine running · 1 account (ok)", "Prices fresh · 2 s old (ok)", "Market open (ok)"]);
  });

  it("says a closed market is expected (grey), with the reopening time, and prices pause", () => {
    const p = systemLine({ accounts: [a([block("market_closed", { at: "2026-10-19T22:00:00Z", at_label: "opens" })])], feeds: [feed(900)], now: NOW });
    expect(p[1]).toMatchObject({ word: "paused while closed", tone: "idle" });
    expect(p[2].tone).toBe("idle");
    expect(p[2].word).toMatch(/^closed · opens (\w{3} )?\d\d:\d\d$/);
  });

  it("marks an old engine report and old prices amber, and an unknown feed grey", () => {
    const old = systemLine({ accounts: [a([], "2026-10-19T09:55:00Z")], feeds: [feed(130)], now: NOW });
    expect(words(old).slice(0, 2)).toEqual(["Engine last report 5 min ago (warn)", "Prices last tick 2 min ago (warn)"]);
    expect(systemLine({ accounts: [a()], feeds: [feed(1e9)], now: NOW })[1]).toMatchObject({ word: "not reported", tone: "idle" });
  });

  it("names a price block and a news blackout; no engine means nothing is known", () => {
    const p = systemLine({ accounts: [a([block("stale_data", { text: "No fresh prices from the broker" }), block("news_blackout")])], now: NOW });
    expect(p[1]).toMatchObject({ word: "No fresh prices from the broker", tone: "warn" });
    expect(p[2]).toMatchObject({ word: "news blackout", tone: "warn" });
    expect(words(systemLine({ accounts: [], now: NOW }))).toEqual(["Engine not reporting (warn)", "Prices not reported (idle)", "Market not known (idle)"]);
    expect(systemLine({ accountsError: true, now: NOW })[0]).toMatchObject({ word: "can't reach the server", tone: "warn" });
  });

  it("counts open trades and lists the accounts that can't take every trade", () => {
    expect([0, 1, 3].map(openTradesWords)).toEqual(["No open trades", "1 open trade", "3 open trades"]);
    expect(notOpen([a(), a([block("weekend")])]).length).toBe(1);
  });
});
