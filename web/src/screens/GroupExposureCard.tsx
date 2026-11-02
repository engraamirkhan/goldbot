// One account's exposure by correlation group (multi-instrument UI spec 2026-11-02, section 4): every group in the
// fixed order, zero included; open notional against the group's cap in the account currency as a meter (role="meter")
// with the % in words, lots by symbol under it; "cap not published" when the engine gives none; the account's heat
// (the loss if every stop on every symbol were hit) against its limit. Red only for a spent cap; a stale report is
// labelled as the LimitCard labels it. Read-only. Not shown for a single-symbol engine (no groups published).
import { cash } from "../lib/account";
import { GROUP_WORDS } from "../lib/instruments";
import { ago, ageSeconds, type LimitAccount } from "../lib/limits";
import { lots } from "../ui/format";

const money = (x: number, ccy: string | null | undefined) => cash(x, ccy).replace(/\.00(?=\D*$)/, "");

export function GroupExposureCard({ a, now }: { a: LimitAccount; now: number }) {
  const groups = a.groups ?? [];
  if (groups.length === 0) return null;
  const ccy = a.currency;
  const age = ageSeconds(a.updated_utc, now);
  return (
    <section className={`group-exposure${a.stale ? " stale" : ""}`} aria-label={`Exposure by group for ${a.account_id}`}>
      <header className="acct-head"><span className="k">Exposure by group</span>
        {a.stale && age != null && <span className="state idle small">{`Updated ${ago(age)}`}</span>}</header>
      <ul className="group-rows">
        {groups.map((g) => {
          const pct = g.cap ? Math.round((g.notional / g.cap) * 100) : null;
          const spent = g.cap != null && g.notional >= g.cap;
          const syms = Object.entries(g.lots_by_symbol ?? {});
          return (
            <li key={g.group} className={`group-row${spent ? " bad" : ""}${g.notional === 0 ? " idle" : ""}`}>
              <p><strong>{GROUP_WORDS[g.group]}</strong>{" "}
                <span className="num">{money(g.notional, ccy)}</span>{" "}
                {g.cap != null ? <span className="small">of {money(g.cap, ccy)} ({pct}%)</span> : <span className="small muted">cap not published</span>}
                {spent && <> <span className="state bad">cap spent: new {GROUP_WORDS[g.group]} entries stop, exits keep working</span></>}</p>
              {g.cap != null && (
                <div className="ddtrack limit-bar" role="meter" aria-label={`${GROUP_WORDS[g.group]} notional against its cap`}
                     aria-valuemin={0} aria-valuemax={g.cap} aria-valuenow={Math.min(g.notional, g.cap)}
                     aria-valuetext={`${money(g.notional, ccy)} of ${money(g.cap, ccy)}, ${pct}%`}>
                  <span className={`ddfill ${spent ? "bad" : "ok"}`} style={{ width: `${Math.min(1, g.notional / g.cap) * 100}%` }} />
                </div>
              )}
              {syms.length > 0 && <p className="small muted num">{syms.map(([s, l]) => `${s} ${lots(l)}`).join(" · ")}</p>}
            </li>
          );
        })}
      </ul>
      <p className="small num"><span className="k">Heat at stops (all symbols)</span>{" "}
        {a.heat_used != null ? money(a.heat_used, ccy) : "not reported"}
        {a.heat_used != null && a.heat_limit != null && ` of ${money(a.heat_limit, ccy)}`}</p>
    </section>
  );
}
