// Shared components of the design system (docs/ux/design-system.md). Styles: src/styles/base.css. Small on purpose:
// presentation only, no data fetching, so every screen composes the same pieces.
import type { ButtonHTMLAttributes, ReactNode } from "react";

export type Tone = "ok" | "warn" | "bad" | "info" | "idle";

const UTC = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", day: "numeric", month: "short", timeZone: "UTC", hour12: false });

/** Local "14:02" with the UTC time on hover; every time on screen goes through this. */
export function TimeText({ iso, withDay = false }: { iso: string; withDay?: boolean }) {
  const d = new Date(iso);
  const local = d.toLocaleString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, ...(withDay ? { day: "numeric", month: "short" } : {}) });
  return <time dateTime={iso} title={`${UTC.format(d)} UTC`}>{local}</time>;
}

const SYMBOL: Record<string, string> = { GBP: "£", USD: "$", EUR: "€" };
/** "£12.40", "−£8.20" (U+2212), "+£3.00" when signed. */
export function money(x: number, ccy?: string | null, signed = false): string {
  const sym = ccy ? SYMBOL[ccy] ?? `${ccy} ` : "";
  const body = `${sym}${Math.abs(x).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (x < 0) return `−${body}`;
  return signed && x > 0 ? `+${body}` : body;
}

export function PageHeader({ title, aside }: { title: string; aside?: ReactNode }) {
  return (
    <div className="page-head">
      <h1>{title}</h1>
      {aside && <span className="muted small">{aside}</span>}
    </div>
  );
}

export function Card({ title, aside, tone, children, label }: {
  title?: ReactNode; aside?: ReactNode; tone?: Tone; children: ReactNode; label?: string;
}) {
  return (
    <section className={`ui-card ${tone ?? ""}`} aria-label={label}>
      {(title || aside) && (
        <header className="ui-card-head">
          {title && <h2>{title}</h2>}
          {aside && <span className="ui-card-aside">{aside}</span>}
        </header>
      )}
      {children}
    </section>
  );
}

export function MetricTile({ label, value, unit, sub, tone }: {
  label: string; value: ReactNode; unit?: string; sub?: ReactNode; tone?: Tone;
}) {
  return (
    <div className={`metric ${tone ?? ""}`}>
      <span className="metric-label">{label}</span>
      <span className="metric-value">{value}{unit && <small>{unit}</small>}</span>
      {sub && <span className="metric-sub">{sub}</span>}
    </div>
  );
}

export function Meter({ value, max, label, tone, text }: { value: number; max: number; label: string; tone?: Tone; text?: string }) {
  const f = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
  return (
    <div className={`ui-meter ${tone ?? ""}`} role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={max}
         aria-valuenow={value} aria-valuetext={text}>
      <span style={{ transform: `scaleX(${f})` }} />
    </div>
  );
}

export function Badge({ tone = "idle", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`ui-badge ${tone}`}>{children}</span>;
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost" | "approve"; size?: "md" | "lg" };
export function Button({ variant = "secondary", size = "md", className = "", type = "button", ...rest }: BtnProps) {
  return <button type={type} className={`btn ${variant} ${size === "lg" ? "lg" : ""} ${className}`} {...rest} />;
}

/** Sub-tabs inside a page (role=tablist). */
export function Segmented<T extends string>({ items, value, onChange, label }: {
  items: { id: T; label: string }[]; value: T; onChange: (id: T) => void; label: string;
}) {
  return (
    <div className="segmented" role="tablist" aria-label={label}>
      {items.map((it) => (
        <button key={it.id} type="button" role="tab" aria-selected={it.id === value} tabIndex={it.id === value ? 0 : -1}
                onClick={() => onChange(it.id)}
                onKeyDown={(e) => {
                  const i = items.findIndex((x) => x.id === value);
                  if (e.key === "ArrowRight") onChange(items[(i + 1) % items.length].id);
                  if (e.key === "ArrowLeft") onChange(items[(i - 1 + items.length) % items.length].id);
                }}>{it.label}</button>
      ))}
    </div>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className="state-box"><strong>{title}</strong>{children && <span>{children}</span>}{action}</div>;
}

/** What failed, what still works, and a retry. Amber: a dashboard error never stops trading. */
export function ErrorState({ title, stillWorks, onRetry }: { title: string; stillWorks?: string; onRetry?: () => void }) {
  return (
    <div className="state-box error" role="alert">
      <strong>{title}</strong>
      {stillWorks && <span>{stillWorks}</span>}
      {onRetry && <Button onClick={onRetry}>Try again</Button>}
    </div>
  );
}

export function Skeleton({ height, label = "Loading" }: { height: number; label?: string }) {
  return <div className="skeleton-box" style={{ height }} aria-busy="true" aria-label={label} />;
}
