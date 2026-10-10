import type { AccountSummary, Status } from "../lib/api";
import { blockers, localTime } from "../lib/approval";

type Tone = "ok" | "warn" | "bad" | "idle";
type Chip = { key: string; label: string; value: string; detail?: string; tone: Tone; title?: string };

const STAGE: Record<AccountSummary["stage"], string> = { normal: "normal", size_down: "size down", halted: "halted" };
const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

/** Everything that can stop a new entry, always visible above the approval cards. Red only for what stops trading. */
export function SafetyStrip({ status, accounts, error }: { status?: Status; accounts?: AccountSummary[]; error?: boolean }) {
  if (error && !status) {
    return <p className="strip-summary warn" role="alert">Safety state unavailable. Check Overview before approving.</p>;
  }
  const loading = !status || !accounts;
  const blocked = loading ? [] : blockers(status, accounts);
  const idle = !loading && accounts.length === 0;
  const chips: Chip[] = loading ? LOADING : build(status, accounts);
  return (
    <section className="safety" aria-label="Safety state">
      <p className={`strip-summary ${loading ? "idle" : blocked.length ? "bad" : idle ? "warn" : "ok"}`} role="status">
        {loading ? "Checking safety state…" : blocked.length ? `New entries blocked: ${blocked.join(", ")}`
          : idle ? "No engines reporting" : "New entries allowed"}
      </p>
      <ul className="strip">
        {chips.map((c) => (
          <li key={c.key} className={`chip ${c.tone}`} title={c.title}>
            <span className="k">{c.label}</span>
            <strong>{c.value}</strong>
            {c.detail && <span className="detail">{c.detail}</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}

const LOADING: Chip[] = ["Owner halt", "Supervisor", "Drift halt", "Drawdown", "News", "Account"].map((label) => (
  { key: label, label, value: "…", tone: "idle" }
));

function build(s: Status, accounts: AccountSummary[]): Chip[] {
  const chips: Chip[] = [
    s.halted
      ? { key: "owner", label: "Owner halt", value: "ON", tone: "bad",
          detail: [s.halted_by, s.halt_reason].filter(Boolean).join(": ") || undefined }
      : { key: "owner", label: "Owner halt", value: "off", tone: "ok" },
    s.supervisor_halt
      ? { key: "sup", label: "Supervisor", value: "HALT", tone: "bad", detail: s.supervisor_reasons.join(", ") || undefined }
      : { key: "sup", label: "Supervisor", value: "ok", tone: "ok" },
    s.drift_halt
      ? { key: "drift", label: "Drift halt", value: "ON", tone: "bad", detail: s.drift_reasons.join("; ") || undefined }
      : { key: "drift", label: "Drift halt", value: "off", tone: "ok" },
  ];
  if (accounts.length === 0) {
    chips.push({ key: "dd", label: "Drawdown", value: "no engines", tone: "warn" });
  } else {
    const worst = accounts.some((a) => a.stage === "halted") ? "bad" : accounts.some((a) => a.stage === "size_down") ? "warn" : "ok";
    const one = accounts.length === 1;
    chips.push({ key: "dd", label: "Drawdown", tone: worst,
                 value: one ? STAGE[accounts[0]!.stage] : accounts.map((a) => `${a.account_id} ${STAGE[a.stage]}`).join(" · "),
                 detail: one ? pct(accounts[0]!.drawdown_pct) : undefined });
  }
  const b = s.blackout;
  if (b) {
    const at = b.ts_utc ?? b.received_utc;
    const t = at ? localTime(at) : null;
    chips.push({ key: "news", label: b.kind === "news_shock" ? "News shock" : "News blackout", value: "ON", tone: "bad",
                 detail: `${b.title}${t ? ` · ${t.text}` : ""}`, title: t?.utc });
  } else {
    chips.push({ key: "news", label: "News", value: "clear", tone: "ok" });
  }
  chips.push(accounts.length === 0
    ? { key: "class", label: "Account", value: "—", tone: "warn" }
    : { key: "class", label: "Account", value: accounts.map((a) => `${a.account_class} · ${a.mode}`).join(" / "),
        tone: accounts.some((a) => a.account_class === "unknown" && a.mode !== "paper") ? "bad" : "ok",
        detail: accounts.length > 1 ? accounts.map((a) => a.account_id).join(" / ") : undefined });
  return chips;
}
