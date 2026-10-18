// TradingView link (docs/ux/limits-and-tradingview.md, card B; top of the Feeds tab) and its Overview chip. Amber,
// never red: TradingView is an optional second feed and cannot stop trading (goldbot/ops/tv_pull_health.py: WARN
// only). Indicator names and counts only, never plot names or values (TvIndicatorStatus).
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import {
  ago, ageSeconds, REPAINT_WORDS, SIGNIN_WORDS, utcClock, zoned, zonedDay, type TvIndicatorStatus, type TvStatusView,
} from "../lib/limits";
import { utcTitle } from "../lib/explain";
import { useNow } from "../lib/useNow";
import { CopyCommand } from "./Health";

const n = (x: number) => x.toLocaleString("en-GB");
const live = (r: TvIndicatorStatus) => r.alerts_24h + r.pull_live_24h;
const history = (r: TvIndicatorStatus) => r.csv_bars + r.pull_history_bars;
const TF_WORDS: Record<string, string> = { "15": "15m", "60": "1h", "240": "4h", "1D": "1d", D: "1d" };
const tf = (t: string) => TF_WORDS[t] ?? t;

function Repaint({ r }: { r: TvIndicatorStatus }) {
  const w = REPAINT_WORDS[r.repaint_status];
  return (
    <>
      <span className={`state ${w.tone}`}>{w.label}</span>
      {r.gaps_24h > 0 && <span className="small warn-text"> {`${r.gaps_24h} missed bar${r.gaps_24h === 1 ? "" : "s"} in 24 h`}</span>}
    </>
  );
}

function Indicators({ rows }: { rows: TvIndicatorStatus[] }) {
  if (rows.length === 0) return <p className="muted">No indicators yet. Alerts or a chart export have not arrived.</p>;
  const key = (r: TvIndicatorStatus) => `${r.indicator}@${r.version}/${r.symbol}/${r.tf}`;
  return (
    <>
      <div className="scroll tv-table"><table>
        <thead><tr><th>Name</th><th>Timeframe</th><th>Live 24 h</th><th>History</th><th>Repaint check</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={key(r)}>
              <td>{r.indicator}</td><td>{tf(r.tf)}</td><td className="num">{n(live(r))}</td>
              <td className="num">{n(history(r))}</td><td><Repaint r={r} /></td>
            </tr>
          ))}
        </tbody>
      </table></div>
      <ul className="tv-cards" aria-label="Indicators">
        {rows.map((r) => (
          <li key={key(r)} className="hyp-card">
            <p className="hyp-head">{r.indicator} <span className="muted">· {tf(r.tf)}</span></p>
            <dl>
              <div><dt>Live bars, last 24 h</dt><dd>{n(live(r))}</dd></div>
              <div><dt>History bars</dt><dd>{n(history(r))}</dd></div>
              <div><dt>Repaint check</dt><dd><Repaint r={r} /></dd></div>
            </dl>
          </li>
        ))}
      </ul>
    </>
  );
}

function SignInNote({ v }: { v: TvStatusView }) {
  if (v.signin === "not_set_up") return <p className="muted small">TradingView is optional; nothing to do.</p>;
  if (v.signin !== "expired" && v.signin !== "unknown") return null;
  return (
    <div className="warn-note" role="status">
      <p>
        {v.signin === "expired" ? "TradingView sign-in expired." : "TradingView sign-in could not be read, so it is treated as expired."}
        {" "}Trading is not affected. History is kept, but no new TradingView data arrives. To fix, run this on the brain:
      </p>
      {v.fix_command && <CopyCommand cmd={v.fix_command} />}
      <p className="small">It clears by itself at the next successful pull.</p>
    </div>
  );
}

export function TradingViewCard() {
  const q = useQuery({ queryKey: ["tradingview"], queryFn: api.tradingview, refetchInterval: 60000 });
  const now = useNow(60000);
  if (q.isLoading) {
    return (
      <section className="acct tv-card skeleton" aria-label="Loading TradingView status" aria-busy="true">
        <header className="acct-head"><span className="sk sk-title" /></header>
        <span className="sk sk-line" /><span className="sk sk-line" />
      </section>
    );
  }
  if (q.isError || !q.data) return <section className="acct tv-card"><p className="warn-text">Could not read TradingView status</p></section>;
  const v = q.data;
  const w = SIGNIN_WORDS[v.signin];
  const age = ageSeconds(v.last_pull_utc, now);
  return (
    <section className="acct tv-card" aria-label="TradingView">
      <header className="acct-head">
        <strong>TradingView</strong>
        <span className={`state ${w.tone}`}>{w.label}</span>
      </header>
      {v.signin !== "not_set_up" && (
        <p className="small">Last successful pull:{" "}
          {v.last_pull_utc && age != null
            ? <><time dateTime={v.last_pull_utc} title={utcTitle(v.last_pull_utc)}>{age > 86400 ? zonedDay(v.last_pull_utc) : zoned(v.last_pull_utc)}</time>{` (${utcClock(v.last_pull_utc)}), ${ago(age)}`}</>
            : "none yet"}
        </p>
      )}
      {v.stale && v.signin === "ok" && age != null && (
        <p className="small warn-text">{`No pull for ${Math.round(age / 3600)} h; one is expected daily.`}</p>
      )}
      <SignInNote v={v} />
      <p className="small tv-counts">
        <span>Indicators found: {v.indicators.length}</span>
        <span>Live bars: {n(v.live_bars_24h)} (last 24 h)</span>
        <span>History bars: {n(v.history_bars)}</span>
      </p>
      <Indicators rows={v.indicators} />
    </section>
  );
}

/** Overview: one chip beside the other feed facts. */
export function TradingViewChip() {
  const q = useQuery({ queryKey: ["tradingview"], queryFn: api.tradingview, refetchInterval: 60000 });
  if (!q.data) return null;
  const v = q.data;
  const tone = v.signin === "ok" ? (v.stale ? "warn" : "ok") : SIGNIN_WORDS[v.signin].tone;
  const word = v.signin === "ok" ? (v.stale ? "signed in, no recent pull" : "signed in") : SIGNIN_WORDS[v.signin].label.toLowerCase();
  return (
    <div className={`chip ${tone === "info" ? "idle" : tone}`}>
      <span className="k">Feed</span>
      <strong>{`TradingView: ${word}`}</strong>
      <span className="detail">optional; never stops trading</span>
    </div>
  );
}
