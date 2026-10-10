import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, hasRole, type AutoModeView, type ModeChangeResult } from "../lib/api";
import { localTime, num, utcTitle } from "../lib/explain";

// Auto-mode card (design: Operating mode, row A10). Auto mode is only offered, never taken: the card shows the same
// evidence check Telegram's /mode runs, and the owner decides. Enabling needs the owner's authenticator code; switching
// back to propose needs nothing. The engines still force propose under the 30-day re-arm lock and the 12% kill switch.

const MODE_WORDS = { auto: "Auto", propose: "Propose-and-approve" } as const;
const r2 = (x: number | null | undefined) => (x == null ? "n/a" : `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(2)}R`);

function Evidence({ v }: { v: AutoModeView }) {
  const t = v.test;
  const conf = `${Math.round((1 - t.alpha) * 100)}%`;
  return (
    <dl className="evidence">
      <div><dt>Decided proposals since the last mode change</dt><dd>{v.decided} of {v.min_proposals} needed <small>({v.approved} approved, {v.rejected} rejected)</small></dd></div>
      <div><dt>RiskGate breaches</dt><dd>{v.breaches.length}{v.breaches.length > 0 && <small>{v.breaches.join("; ")}</small>}</dd></div>
      <div><dt>Mean outcome, approved vs rejected</dt><dd>{r2(t.mean_r_approved)} <small>(n={t.n_approved})</small> vs {r2(t.mean_r_rejected)} <small>(n={t.n_rejected})</small></dd></div>
      <div><dt>Difference ({conf} interval)</dt><dd>{t.diff == null ? "n/a" : <>{r2(t.diff)} <small>[{r2(t.ci_low)}, {r2(t.ci_high)}]</small></>}</dd></div>
      <div><dt>Welch p (needs ≥ {t.alpha})</dt><dd>{num(t.p_value)} <small>{t.p_value == null ? `needs ${v.min_outcomes_per_side} outcomes a side` : t.indistinguishable ? "no difference: the veto adds nothing" : "the veto adds value"}</small></dd></div>
    </dl>
  );
}

function Forced({ v }: { v: AutoModeView }) {
  if (v.forced_propose.length === 0) return null;
  return (
    <div className="warn-note" role="note">
      <strong>Entries wait for your click on these accounts whatever the mode says:</strong>
      <ul>
        {v.forced_propose.map((f) => (
          <li key={`${f.account_id}-${f.kind}`}>
            {f.account_id}: {f.detail}
            {f.until && <> until <time title={utcTitle(f.until)}>{localTime(f.until)}</time></>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function OwnerControls({ v }: { v: AutoModeView }) {
  const qc = useQueryClient();
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const done = (res: ModeChangeResult) => { qc.setQueryData(["automode"], res.view); qc.invalidateQueries({ queryKey: ["status"] }); setCode(""); setMsg(res.message); };
  const enable = useMutation({ mutationFn: () => api.setMode("auto", code), onSuccess: done, onMutate: () => setMsg(null) });
  const propose = useMutation({ mutationFn: () => api.setMode("propose"), onSuccess: done, onMutate: () => setMsg(null) });
  const err = (enable.error ?? propose.error) as Error | null;
  const autoNow = v.owner_mode === "auto" || Object.values(v.engine_modes).includes("auto");
  return (
    <div className="mode-actions">
      {autoNow && (
        <button type="button" className="secondary" disabled={propose.isPending} onClick={() => propose.mutate()}>Switch to propose</button>
      )}
      {v.can_enable && (
        <form onSubmit={(e) => { e.preventDefault(); enable.mutate(); }}>
          <p className="small">
            Auto mode places every entry that passes the RiskGate without asking you. Exits stay automatic. A 12% drawdown,
            or a re-arm, returns to propose. You can switch back any time, no code needed.
          </p>
          <div className="row">
            <input aria-label="Code to enable auto mode" inputMode="numeric" autoComplete="one-time-code" maxLength={6}
              placeholder="authenticator code" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
            <button type="submit" disabled={code.length !== 6 || enable.isPending}>Enable auto mode</button>
          </div>
        </form>
      )}
      {msg && <p className="outcome ok" role="status">{msg}</p>}
      {err && <p className="err" role="alert">{err.message}</p>}
    </div>
  );
}

export function AutoModeCard() {
  const q = useQuery({ queryKey: ["automode"], queryFn: api.automode, refetchInterval: 60000 });
  if (q.isLoading) return <section className="mode-card" aria-label="Entry mode"><p className="muted" role="status">Loading entry mode…</p></section>;
  if (q.error || !q.data) {
    return <section className="mode-card" aria-label="Entry mode"><p className="err" role="alert">Could not load the entry mode ({(q.error as Error | null)?.message ?? "no data"}).</p></section>;
  }
  const v = q.data;
  const engines = Object.entries(v.engine_modes);
  const mode = v.owner_mode ?? "propose";
  return (
    <section className="mode-card" aria-labelledby="mode-h">
      <div className="ddhead">
        <h2 id="mode-h">Entry mode: {MODE_WORDS[mode]}{v.owner_mode == null && <span className="muted small"> (engine setting)</span>}</h2>
        <span className={`state ${v.eligible ? "ok" : "idle"}`}>{v.eligible ? (v.owner_mode === "auto" ? "evidence holds" : "auto mode offered") : "auto mode not offered"}</span>
      </div>
      <p className="muted small">
        {v.mode_since ? <>Set <time title={utcTitle(v.mode_since)}>{localTime(v.mode_since)}</time>{v.mode_by ? ` by ${v.mode_by}` : ""}{v.mode_reason ? ` (${v.mode_reason})` : ""}. </> : "The owner has not set a mode yet. "}
        {engines.length > 0 ? <>Engines now: {engines.map(([a, m]) => `${a} ${m}`).join(", ")}.</> : "No engines reporting."}
      </p>
      <Forced v={v} />
      {v.eligible
        ? <p className="small">Offered: after {v.decided} decided proposals with no RiskGate breach, approved and rejected trades turned out the same, so your veto is no longer adding value. You decide.</p>
        : <><p className="small">Not offered yet, because:</p><ul className="reasons-list">{v.reasons.map((x) => <li key={x}>{x}</li>)}</ul></>}
      <details className="as-table"><summary>Evidence</summary><Evidence v={v} /></details>
      {hasRole("owner") ? <OwnerControls v={v} /> : <p className="muted small">Only the owner can change the mode.</p>}
    </section>
  );
}
