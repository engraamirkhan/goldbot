import type { AccountSummary, EntryBlock } from "../lib/api";
import { BADGE, TONE, utcText, whenText } from "../lib/entry";
import { useNow } from "../lib/useNow";

type Props = { accounts?: AccountSummary[]; loading?: boolean; error?: boolean; now?: number };

/** Can each account take a new trade right now; if not, why and what fixes it (docs/ux/entry-status.md).
 *  The API words every reason (goldbot/api/entry_status.py); this only lays it out. */
export function EntryStatusPanel({ accounts, loading, error, now }: Props) {
  const tick = useNow(30_000);
  const t = now ?? tick;
  return (
    <section className="entry" aria-labelledby="entry-h">
      <h2 id="entry-h">Entry status</h2>
      {loading ? (
        <div className="entry-row idle skel" aria-busy="true" aria-label="Loading entry status">
          <span className="sk sk-title" /><span className="sk sk-line" />
        </div>
      ) : error && !accounts ? (
        <p className="entry-row warn" role="alert">
          Entry status unavailable: the dashboard can't reach the server (retrying). Exits keep running on the engine.
        </p>
      ) : (accounts?.length ?? 0) === 0 ? (
        <p className="entry-row warn">
          No engine is reporting, so no new trades can open. Start the engine; the Health tab shows what is running.
        </p>
      ) : (
        accounts!.map((a) => <Row key={a.account_id} a={a} now={t} />)
      )}
    </section>
  );
}

function Row({ a, now }: { a: AccountSummary; now: number }) {
  const s = a.entry_status;
  const name = `Entry status ${a.account_id}`;
  if (!s) {
    return (
      <article className="entry-row idle" aria-label={name}>
        <header><strong>{a.account_id}</strong> <span className="muted">{a.mode}</span></header>
        <p className="muted">Entry status not reported by this server version.</p>
      </article>
    );
  }
  return (
    <article className={`entry-row ${TONE[s.state]}`} aria-label={name}>
      <header>
        <span className="entry-badge">{BADGE[s.state]}</span>
        <strong>{a.account_id}</strong>
        <span className="muted">{a.mode}</span>
        {s.as_of && <AsOf iso={s.as_of} />}
      </header>
      <p className="entry-headline">{s.headline}</p>
      {s.blocks.length > 0 && (
        <ul className="entry-blocks">{s.blocks.map((b) => <Block key={`${b.code}:${b.agent_id ?? ""}`} b={b} now={now} />)}</ul>
      )}
      {s.notes.map((n) => <p key={n} className="entry-note">{n}</p>)}
      <p className="entry-exits">{s.exits}</p>
    </article>
  );
}

function AsOf({ iso }: { iso: string }) {
  const local = new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  return <time className="muted small as-of" dateTime={iso} title={utcText(iso)}>as of {local}</time>;
}

function Block({ b, now }: { b: EntryBlock; now: number }) {
  const when = b.at && b.at_label ? whenText(b.at, b.at_label, now) : null;
  return (
    <li className={b.kind}>
      <span className="what">
        {b.kind === "act" && <span className="needs">Needs you</span>}
        {b.text}
        {when && <> · <time dateTime={b.at!} title={when.utc}>{when.text}</time></>}
      </span>
      <span className="fix">
        {b.fix}
        {b.runbook_url && <> <a href={b.runbook_url} target="_blank" rel="noreferrer noopener">Runbook: {b.runbook}</a></>}
      </span>
    </li>
  );
}
