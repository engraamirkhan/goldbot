// The header's one-line safety state, on every screen (docs/ux/dashboard-v2.md). A tap opens what blocks entries and
// the existing Halt / Re-arm control (HaltControl, re-hosted unchanged): Halt is two taps from anywhere.
// Red only when something stops new entries; amber when the dashboard can't tell; never "fine" from stale data.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, type AccountSummary, type Status } from "../lib/api";
import { blockers } from "../lib/approval";
import { STALE_MS } from "../lib/entry";
import { useNow } from "../lib/useNow";
import { HaltControl } from "../screens/HaltControl";
import { NO_ENGINE } from "../screens/SafetyStrip";

type Tone = "ok" | "warn" | "bad" | "idle";

const SHUT = new Set(["market_closed", "weekend"]);
/** Every account is blocked only because the market is shut, and nothing else (no halt, no blackout) is on. */
function marketShut(s: Status, a: AccountSummary[]): boolean {
  if (s.halted || s.supervisor_halt || s.drift_halt || s.blackout || a.length === 0) return false;
  return a.every((x) => (x.entry_status?.blocks.length ?? 0) > 0 && x.entry_status!.blocks.every((b) => SHUT.has(b.code)));
}

export function StatusPill({ onOpenTrades }: { onOpenTrades?: () => void }) {
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  const now = useNow(5_000);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);

  const s = status.data, a = accounts.data;
  const updated = Math.min(status.dataUpdatedAt || 0, accounts.dataUpdatedAt || 0);
  const stale = !!s && !!a && (status.isError || accounts.isError || (updated > 0 && now - updated > STALE_MS));
  const blocked = s && a ? blockers(s, a) : [];
  const waiting = s?.pending ?? 0;
  let tone: Tone, text: string;
  if (!s || !a) {
    [tone, text] = status.isError || accounts.isError ? ["warn", "Can't reach goldbot"] : ["idle", "Checking safety…"];
  } else if (stale) {
    [tone, text] = ["warn", "Status not up to date"];
  } else if (blocked.length && marketShut(s, a)) {
    // the market is shut (weeknight close or weekend): expected, nothing to do, so calm grey, not red
    const at = a.flatMap((x) => x.entry_status?.blocks ?? []).find((b) => b.at && b.at_label === "opens")?.at;
    const opens = at ? new Date(at).toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }) : null;
    [tone, text] = ["idle", opens ? `Market closed · opens ${opens}` : "Market closed"];
  } else if (blocked.length) {
    [tone, text] = ["bad", `Entries blocked: ${blocked.join(", ")}`];
  } else if (a.length === 0) {
    [tone, text] = ["warn", NO_ENGINE];
  } else {
    [tone, text] = ["ok", waiting > 0 ? `Trading normally · ${waiting} waiting` : "Trading normally"];
  }

  return (
    <div className="pill-slot" ref={box}>
      <button type="button" className={`pill ${tone}`} data-status-pill aria-expanded={open} aria-haspopup="true"
              onClick={() => setOpen(!open)}>
        <span className="dot" aria-hidden="true" />
        <span className="txt" aria-live="polite">{text}</span>
        <span className="chev" aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="pill-menu" role="group" aria-label="Safety and halt">
          <h2>Safety now</h2>
          {!s || !a ? <p className="muted small">{text}.</p> : (
            <ul>
              {blocked.length === 0
                ? <li><span aria-hidden="true">✓</span> Nothing is blocking new entries.</li>
                : blocked.map((b) => <li key={b}><span aria-hidden="true">✕</span> {b}</li>)}
              <li><span aria-hidden="true">•</span> Exits always run on the server, whatever this page shows.</li>
            </ul>
          )}
          {waiting > 0 && onOpenTrades && (
            <button type="button" className="btn primary" onClick={() => { setOpen(false); onOpenTrades(); }}>
              {waiting === 1 ? "1 trade waiting for you" : `${waiting} trades waiting for you`}
            </button>
          )}
          {s && <HaltControl status={s} />}
        </div>
      )}
    </div>
  );
}
