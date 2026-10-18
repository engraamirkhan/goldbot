// Limits tab and Overview strip (docs/ux/limits-and-tradingview.md, card A): what the risk limits are now and why they
// last changed. Read-only: nothing here has a button that changes a limit. Red only for a spent cap (it stops new
// entries); a revert is information. Every number has its unit, every time its zone (UTC in the title).
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type ApiError } from "../lib/api";
import { cash } from "../lib/account";
import {
  ago, ageSeconds, capTone, FIELD_WORDS, pctText, SOURCE_WORDS, utcClock, zoned, zonedDay,
  type LimitAccount, type LimitChange, type LimitsView,
} from "../lib/limits";
import { utcTitle } from "../lib/explain";
import { useNow } from "../lib/useNow";

/** "£50", "£12.40": whole amounts without pence. */
const money = (x: number, ccy: string | null | undefined) => cash(x, ccy).replace(/\.00(?=\D*$)/, "");
const value = (field: LimitChange["field"], v: number | null | undefined) =>
  v == null ? (field === "min_lot_risk_cap" ? "off" : "—") : pctText(v);

function Time({ iso, day = false }: { iso: string; day?: boolean }) {
  return <time dateTime={iso} title={utcTitle(iso)}>{day ? zonedDay(iso) : zoned(iso)}</time>;
}

function Cap({ label, period, pct, limit, used, base, baseWhen, resets, ccy, near }: {
  label: string; period: "Today's" | "This week's"; pct: number | null | undefined; limit: number | null | undefined;
  used: number | null | undefined; base: number | null | undefined; baseWhen: string; resets: string;
  ccy: string | null | undefined; near: string;
}) {
  const tone = capTone(used, limit);
  const share = limit && used != null ? Math.min(1, used / limit) : 0;
  const pctUsed = limit && used != null ? Math.round((used / limit) * 100) : null;
  return (
    <div className="limit-row">
      <p>
        <span className="k">{label}</span>{" "}
        <strong>{limit != null ? money(limit, ccy) : "not reported"}</strong>{" "}
        {pct != null && <span className="small">({pctText(pct)}{base != null ? ` of ${money(base, ccy)} at ${baseWhen}` : ""})</span>}
        {tone === "warn" && <> <span className="state warn">{near}</span></>}
      </p>
      {limit != null && used != null && (
        <>
          <div className={`ddtrack limit-bar`} role="meter" aria-label={`${period} loss limit used`} aria-valuemin={0}
               aria-valuemax={limit} aria-valuenow={Math.min(used, limit)}
               aria-valuetext={`${money(used, ccy)} of ${money(limit, ccy)} used, ${pctUsed}%`}>
            <span className={`ddfill ${tone === "idle" ? "ok" : tone}`} style={{ width: `${share * 100}%` }} />
          </div>
          <p className="small">used {money(used, ccy)} ({pctUsed}%) · {resets}</p>
        </>
      )}
    </div>
  );
}

function ChangeLine({ c }: { c: LimitChange }) {
  return (
    <>
      <Time iso={c.ts} day /> · {FIELD_WORDS[c.field]} {value(c.field, c.old_value)} -&gt; {value(c.field, c.new_value)}
      {" "}<span className="muted">by {SOURCE_WORDS[c.source]}</span>
      <br /><span className="small">Why: {c.reason}</span>
    </>
  );
}

function AllChanges({ account }: { account: string }) {
  const q = useQuery({ queryKey: ["limits", "changes", account], queryFn: () => api.limitChanges({ account, limit: 20 }) });
  if (q.isLoading) return <p className="muted small" role="status">Loading changes…</p>;
  if (q.isError) return <p className="err small">Could not read the change log ({(q.error as ApiError).message}).</p>;
  if (!q.data?.length) return <p className="muted small">No limit changes yet. Limits are the profile defaults.</p>;
  return (
    <ol className="limit-changes small" aria-label={`Limit changes for ${account}`}>
      {q.data.map((c, i) => <li key={`${c.ts}-${c.field}-${i}`}><ChangeLine c={c} /></li>)}
    </ol>
  );
}

function Revert({ a }: { a: LimitAccount }) {
  if (a.revert_active) {
    return (
      <div className="info-note" role="note">
        <p>Limits are on the default profile (8% revert active since {a.revert_since ? <Time iso={a.revert_since} day /> : "a restart"}).</p>
        {a.revert_reason && <p className="small">{a.revert_reason} Only your re-arm, at a normal stage, brings the approved limits back.</p>}
      </div>
    );
  }
  if (a.profile === "default") {
    return <p className="small">Auto-revert at 8%: not needed, the limits are the defaults.</p>;
  }
  return (
    <p className="small"><span className="state ok">Auto-revert at 8%: ON</span>. Limits go back to default if the
      account falls 8% from its peak.</p>
  );
}

export function LimitCard({ a, now }: { a: LimitAccount; now: number }) {
  const ccy = a.currency;
  const age = ageSeconds(a.updated_utc, now);
  const hit = a.cap_hit !== "none";
  const reset = a.cap_hit === "weekly" ? a.weekly_resets_utc : a.daily_resets_utc;
  return (
    <section className={`acct limit-card ${hit ? "bad" : ""}`} aria-label={`Limits for ${a.account_id}`}>
      <header className="acct-head">
        <strong>{a.account_id}</strong>
        <span className="tag">{a.mode === "demo" ? "Demo" : a.mode === "live" ? "Live" : "Paper"}</span>
        <span className="small">Profile: {a.profile}</span>
        {a.stale && age != null
          ? <span className="state idle small">{`Updated ${ago(age)}`}</span>
          : a.updated_utc && <span className="muted small">Updated <Time iso={a.updated_utc} /> ({utcClock(a.updated_utc)})</span>}
      </header>
      {a.error ? (
        <p className="err">Could not read the limits ({a.error}). Engines fail closed on this; see Health.</p>
      ) : (
        <>
          {hit && (
            <div className="banner bad" role="alert">
              {a.cap_hit === "daily" ? "Daily" : "Weekly"} loss limit reached. New entries are stopped until{" "}
              {utcClock(reset).replace(" UTC", "")} UTC ({zoned(reset)}). Exits keep working.
            </div>
          )}
          <Cap label="Today's loss limit" period="Today's" pct={a.daily_cap_pct} limit={a.daily_limit} used={a.daily_used}
               base={a.day_start_equity} baseWhen="today's start" ccy={ccy} near="Close to today's limit"
               resets={`day resets ${utcClock(a.daily_resets_utc)} (${zoned(a.daily_resets_utc)})`} />
          <Cap label="This week's limit" period="This week's" pct={a.weekly_cap_pct} limit={a.weekly_limit} used={a.weekly_used}
               base={a.week_start_equity} baseWhen="the week's start" ccy={ccy} near="Close to this week's limit"
               resets={`week resets Sun ${utcClock(a.weekly_resets_utc)} (${zoned(a.weekly_resets_utc)})`} />
          <p className="small">
            <span className="k">Smallest trade</span>{" "}
            {a.min_lot != null ? `${a.min_lot} lot minimum` : "lot size not reported"}
            {a.min_lot != null && a.min_lot_risk != null && `. A ${a.min_lot} lot trade risks ${money(a.min_lot_risk, ccy)} at the current stop`}
            {a.absolute_daily_limit != null && `, within your ${money(a.absolute_daily_limit, ccy)} day`}.
            {" "}{a.min_lot_risk_cap != null
              ? `Minimum-lot exception: allowed up to ${pctText(a.min_lot_risk_cap)} of the account when that is above the normal risk per trade.`
              : "Minimum-lot exception: off."}
          </p>
          <Revert a={a} />
        </>
      )}
      <LastChange a={a} />
    </section>
  );
}

function LastChange({ a }: { a: LimitAccount }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="limit-last small">
      {a.last_change ? <p><span className="k">Last change</span> <ChangeLine c={a.last_change} /></p>
        : <p className="muted">No limit changes yet. Limits are the profile defaults.</p>}
      <button type="button" className="link" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "Hide changes" : "See all changes"}
      </button>
      {open && <AllChanges account={a.account_id} />}
    </div>
  );
}

function Supervisor({ v }: { v: LimitsView }) {
  const s = v.supervisor;
  return (
    <p className="small muted">
      {s.daily_cap != null && s.weekly_cap != null
        ? `All accounts together: daily ${pctText(s.daily_cap)}, weekly ${pctText(s.weekly_cap)}`
        : "All accounts together: the supervisor is not reporting"}
      {` (defaults ${pctText(s.default_daily)} / ${pctText(s.default_weekly)}; demo envelope ${pctText(s.envelope_daily)} / ${pctText(s.envelope_weekly)}). `}
      The combined cap binds before any one account's.
    </p>
  );
}

export function LimitCardSkeleton() {
  return (
    <section className="acct limit-card skeleton" aria-label="Loading limits" aria-busy="true">
      <header className="acct-head"><span className="sk sk-title" /></header>
      {[0, 1, 2, 3].map((i) => <span key={i} className="sk sk-line" />)}
    </section>
  );
}

const useLimits = () => useQuery({ queryKey: ["limits"], queryFn: api.limits, refetchInterval: 30000 });

export function Limits() {
  const q = useLimits();
  const now = useNow(30000);
  if (q.isLoading) return <section><div className="accts"><LimitCardSkeleton /></div></section>;
  if (q.isError) {
    return (
      <section>
        <p className="err">Could not read the limits ({(q.error as ApiError).message}). Engines fail closed on this; see Health.</p>
        <button type="button" onClick={() => q.refetch()}>Retry</button>
      </section>
    );
  }
  const v = q.data!;
  return (
    <section>
      <h2>Risk limits</h2>
      {v.error && <p className="warn-note small">The change log could not be read ({v.error}); the current limits are still shown.</p>}
      {v.accounts.length === 0 ? <p className="muted">No engines reporting yet.</p> : (
        <div className="accts">{v.accounts.map((a) => <LimitCard key={a.account_id} a={a} now={now} />)}</div>
      )}
      <Supervisor v={v} />
    </section>
  );
}

/** Overview: one line per account; a tap opens the Limits tab. */
export function LimitsStrip({ onOpen }: { onOpen: () => void }) {
  const q = useLimits();
  if (!q.data?.accounts.length) return null;
  return (
    <ul className="limits-strip" aria-label="Limits">
      {q.data.accounts.map((a) => {
        const ccy = a.currency;
        const tone = a.cap_hit !== "none" ? "bad" : [capTone(a.daily_used, a.daily_limit), capTone(a.weekly_used, a.weekly_limit)].includes("warn") ? "warn" : "ok";
        const day = a.daily_limit != null && a.daily_used != null ? `today ${money(a.daily_used, ccy)} of ${money(a.daily_limit, ccy)} used` : "today not reported";
        const week = a.weekly_limit != null && a.weekly_used != null ? `week ${money(a.weekly_used, ccy)} of ${money(a.weekly_limit, ccy)}` : "week not reported";
        const word = tone === "bad" ? "limit reached" : tone === "warn" ? "close to a limit" : "within limits";
        return (
          <li key={a.account_id}>
            <button type="button" className={`chip ${tone}`} onClick={onOpen}>
              <span className="k">Limits</span>
              <strong>{`${a.account_id}: ${day}, ${week}`}</strong>
              <span className="detail">{word}{a.revert_active ? " · default profile (8% revert)" : ""}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
