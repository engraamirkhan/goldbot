import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type ActiveBlackout, type CalendarEvent, type Headline } from "../lib/api";
import { useNow } from "../lib/useNow";

// Times are shown in UTC: blackout windows, the engines and the calendar archive all work in UTC.
const UTC_FMT = new Intl.DateTimeFormat("en-GB", {
  weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC",
});
const UTC_HM = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC" });
const utc = (iso: string) => UTC_FMT.format(new Date(iso));
const hm = (iso: string) => UTC_HM.format(new Date(iso));

const DAYS = [1, 3, 7, 14] as const;
const RELEVANCE = [
  { label: "all, incl. unscored", value: 0 }, { label: "≥ 0.3", value: 0.3 }, { label: "≥ 0.5", value: 0.5 }, { label: "≥ 0.7", value: 0.7 },
] as const;

function BlackoutBanner({ b }: { b: ActiveBlackout }) {
  const what = b.kind === "news_shock"
    ? `news shock “${b.title}”${b.received_utc ? ` received ${hm(b.received_utc)} UTC` : ""}`
    : `${b.title}${b.ts_utc ? ` at ${hm(b.ts_utc)} UTC` : ""}`;
  return (
    <div className="banner bad" role="alert">
      <strong>Entry blackout active</strong> — {what}. New entries are blocked on {b.accounts.join(", ")}; exits are unaffected.
    </div>
  );
}

function inWindow(e: CalendarEvent, now: number): boolean {
  if (!e.blackout_start || !e.blackout_end) return false;
  return new Date(e.blackout_start).getTime() <= now && now <= new Date(e.blackout_end).getTime();
}

function CalendarTable({ events, now }: { events: CalendarEvent[]; now: number }) {
  return (
    <div className="scroll"><table>
      <thead><tr><th>Time (UTC)</th><th>Tier</th><th>Ccy</th><th>Event</th><th>Forecast</th><th>Previous</th><th>Entry blackout (UTC)</th></tr></thead>
      <tbody>
        {events.map((e) => {
          const active = inWindow(e, now);
          const past = new Date(e.ts_utc).getTime() < now;
          return (
            <tr key={e.event_id} className={[active ? "bad" : "", past && !active ? "past" : ""].join(" ").trim() || undefined}>
              <td className="nowrap">{utc(e.ts_utc)}</td>
              <td><span className={`badge tier${e.tier}`} title={e.tier === 1 ? "blackout list: US CPI, NFP, FOMC, PCE" : e.impact}>T{e.tier}</span></td>
              <td>{e.country}</td><td>{e.title}</td><td>{e.forecast || "—"}</td><td>{e.previous || "—"}</td>
              <td className="nowrap">
                {e.blackout_start && e.blackout_end ? <>{hm(e.blackout_start)}–{hm(e.blackout_end)}{active && <strong> · now</strong>}</> : "—"}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table></div>
  );
}

const TAG_LABELS: Record<string, string> = {
  hawkish: "hawkish", dovish: "dovish", risk_on: "risk-on", risk_off: "risk-off", positive: "USD+", negative: "USD−",
  beat: "beat", miss: "miss", inline: "inline",
};

/** Direction tags worth showing: neutral/none carry no information. */
function headlineTags(h: Headline): string[] {
  return [h.rates, h.risk, h.dollar, h.surprise].filter((t): t is NonNullable<typeof t> => !!t && t !== "neutral" && t !== "none")
    .map((t) => TAG_LABELS[t] ?? t);
}

function HeadlineList({ items }: { items: Headline[] }) {
  return (
    <div className="scroll"><table>
      <thead><tr><th>Received (UTC)</th><th>Source</th><th>Relevance</th><th>Headline</th><th>Tags</th></tr></thead>
      <tbody>
        {items.map((h) => (
          <tr key={h.item_id} className={h.shock ? "shock" : h.relevance == null ? "past" : undefined}>
            <td className="nowrap">{utc(h.received_utc)}</td>
            <td className="muted">{h.source}</td>
            <td>{h.relevance == null ? <span className="muted">unscored</span> : <meter min={0} max={1} value={h.relevance} title={h.relevance.toFixed(2)} />}{" "}
              {h.relevance != null && h.relevance.toFixed(2)}</td>
            <td>{h.shock && <span className="badge shock">SHOCK</span>} {h.link ? <a href={h.link} target="_blank" rel="noreferrer">{h.title}</a> : h.title}</td>
            <td>{headlineTags(h).map((t) => <span key={t} className="tag">{t}</span>)}</td>
          </tr>
        ))}
      </tbody>
    </table></div>
  );
}

export function News() {
  const [days, setDays] = useState<number>(7);
  const [minRel, setMinRel] = useState<number>(0);
  const now = useNow(30000);
  const cal = useQuery({ queryKey: ["calendar", days], queryFn: () => api.calendar({ days }), refetchInterval: 60000 });
  const news = useQuery({ queryKey: ["news", minRel], queryFn: () => api.news({ hours: 24, min_relevance: minRel }), refetchInterval: 30000 });
  const events = cal.data?.events ?? [];
  const items = news.data ?? [];
  return (
    <section>
      {cal.data?.active_blackout && <BlackoutBanner b={cal.data.active_blackout} />}
      <div className="section-head">
        <h2>Economic calendar</h2>
        <label>Next{" "}
          <select aria-label="Calendar days" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {DAYS.map((d) => <option key={d} value={d}>{d === 1 ? "1 day" : `${d} days`}</option>)}
          </select>
        </label>
      </div>
      {cal.isError ? <p className="err">{String(cal.error)}</p> : events.length === 0 ? (
        <p className="muted">{cal.isLoading ? "Loading…" : cal.data?.note ?? "No tier 1–2 events in this window."}</p>
      ) : <CalendarTable events={events} now={now} />}

      <div className="section-head">
        <h2>Headlines, last 24 h</h2>
        <label>Relevance{" "}
          <select aria-label="Minimum relevance" value={minRel} onChange={(e) => setMinRel(Number(e.target.value))}>
            {RELEVANCE.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </label>
      </div>
      {news.isError ? <p className="err">{String(news.error)}</p> : items.length === 0 ? (
        <p className="muted">{news.isLoading ? "Loading…" : "No headlines collected in this window."}</p>
      ) : <HeadlineList items={items} />}
    </section>
  );
}
