// One trade, fully explained (docs/design/trades-ux-2026-10-27.md §3.3, backlog 133): a sheet on a phone, a 480 px
// panel on a desktop. Levels on the bars, why it was taken, what closed it, what it cost, whether it counts toward its
// arm's read, and the ids under Details. A blinded arm's single trade still shows its own result (ADR 0050: one trade
// is the owner's money, not an aggregate).
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api, ApiError } from "../../lib/api";
import { modeBadge } from "../../lib/home";
import {
  duration, EVIDENCE_WORDS, exitWords, KIND_WORDS, moneyOrDash, POLL, price, resultOf, sideArrow, sideWords, signedR,
  strategyLabel, type TradeRow,
} from "../../lib/trades";
import { useWide } from "../../lib/useMedia";
import { Badge, Button, ErrorState, Skeleton, TimeText } from "../../ui";
import { lots, money } from "../../ui/format";
import { TradeChart } from "./TradeChart";

export function TradeDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const wide = useWide();
  const q = useQuery({ queryKey: ["trade", id], queryFn: () => api.trade(id), refetchInterval: (qq) => (qq.state.data?.status === "open" ? POLL.detail : false) });
  const head = useRef<HTMLHeadingElement>(null);
  useEffect(() => { head.current?.focus(); }, [id]);
  useEffect(() => {
    const on = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [onClose]);
  const t = q.data;
  const missing = q.error instanceof ApiError && q.error.status === 404;
  return (
    <>
      {!wide && <div className="sheet-scrim" aria-hidden="true" onClick={onClose} />}
      <aside className={wide ? "trade-panel" : "trade-sheet"} role="dialog" aria-modal={!wide} aria-label="Trade detail">
        <div className="detail-head">
          <h2 ref={head} tabIndex={-1}>{t ? strategyLabel(t.family_label, t.family, t.timeframe, t.variant) : "Trade"}</h2>
          <Button variant="ghost" onClick={onClose} aria-label="Close trade detail">Close</Button>
        </div>
        {q.isLoading ? <Skeleton height={wide ? 420 : 360} label="Loading the trade" />
          : missing ? <ErrorState title="This trade isn't in the ledger." stillWorks="It may be older than the ledger keeps; the list shows what is stored." />
          : q.isError && !t ? <ErrorState title="Couldn't load this trade." stillWorks="Trading is not affected." onRetry={() => q.refetch()} />
          : <DetailBody t={t!} wide={wide} />}
      </aside>
    </>
  );
}

function DetailBody({ t, wide }: { t: TradeRow; wide: boolean }) {
  const { pnl, r } = resultOf(t);
  const c = t.costs;
  const part = (x: number | null, signed = false) => (x == null ? "not recorded" : money(x, t.currency, signed));
  return (
    <div className="detail-body">
      <p className="detail-sub">
        <Badge tone={modeBadge(t.mode) === "REAL" ? "warn" : "info"}>{modeBadge(t.mode)}</Badge>{" "}
        {t.account_label} · {KIND_WORDS[t.kind]}{t.is_control ? " (random control)" : ""}{" "}
        <strong>{t.status === "open" ? "OPEN" : `CLOSED · ${exitWords(t.exit_reason)}`}</strong>
      </p>
      <p className="detail-money num">
        <span className={`tr-side ${t.side}`}><span aria-hidden="true">{sideArrow(t.side)}</span> {sideWords(t.side)}</span> {t.lots == null ? "" : lots(t.lots)}{" "}
        <strong className="detail-pnl">{moneyOrDash(pnl, t.currency)}</strong> {signedR(r)}{" "}
        <span className="muted">(risk {moneyOrDash(t.risk_amount, t.currency, false)}){t.status === "open" ? " · now" : ""}</span>
      </p>
      {t.stale && <p className="small state warn">Figures are old: the engine isn't reporting; not shown as current.</p>}
      {/* no bars endpoint yet (backlog 133): the chart box shows the levels in words, at its final height */}
      <TradeChart trade={t} bars={null} loading={false} height={wide ? 280 : 200} width={wide ? 440 : 340} />
      <dl className="detail-facts">
        <div><dt>Why taken</dt><dd>{t.why || "—"}</dd></div>
        <div><dt>{t.status === "open" ? "Closes at" : "Closed by"}</dt><dd>{t.status === "open"
          ? <>target {price(t.target)} or stop {price(t.stop_now ?? t.stop)} USD/oz{t.hold_limit_s ? `, or after ${duration(t.hold_limit_s)}` : ""}</>
          : <>{exitWords(t.exit_reason)}, after {duration(t.held_s)}{t.hold_limit_s ? ` (hold limit ${duration(t.hold_limit_s)})` : ""}{t.exit != null ? ` at ${price(t.exit)} USD/oz` : ""}</>}</dd></div>
        <div><dt>Levels</dt><dd className="num">entry {price(t.entry)} · stop {price(t.stop)}{t.stop_now != null && t.stop_now !== t.stop ? ` (moved to ${price(t.stop_now)})` : ""} · target {price(t.target)} USD/oz</dd></div>
        <div><dt>Costs</dt><dd className="num">{c ? <>spread {part(c.spread)}{c.spread_exit_estimated ? " (exit half estimated)" : ""} · slippage {part(c.slippage, true)}{c.slippage_exit_known ? "" : " (entry only)"} · commission {part(c.commission)} · swap {part(c.swap, true)} = {money(c.total, t.currency)}
          {!c.complete && <span className="muted"> (known parts only)</span>}{t.gross != null && <span className="muted"> · gross {money(t.gross, t.currency, true)}</span>}</>
          : t.kind === "shadow" ? "none: a paper trade" : "not recorded"}</dd></div>
        <div><dt>Evidence</dt><dd>{EVIDENCE_WORDS[t.evidence] ?? t.evidence}
          {t.evidence === "excluded" && t.excluded_reason ? `: ${t.excluded_reason}` : ""}</dd></div>
        <div><dt>Times</dt><dd>opened <TimeText iso={t.opened_utc} withDay />{t.exit_utc && <> · closed <TimeText iso={t.exit_utc} withDay /></>} <span className="muted">(your time; UTC on hover)</span></dd></div>
      </dl>
      {t.arm_id && <p className="small muted">This arm's totals stay hidden until its read (ADR 0050); one trade is shown in full.</p>}
      <details className="detail-ids">
        <summary>Details</summary>
        <dl className="small">
          {t.arm_id && <div><dt>Arm</dt><dd className="mono">{t.arm_id}</dd></div>}
          <div><dt>Trade</dt><dd className="mono">{t.trade_id}</dd></div>
          <div><dt>Family</dt><dd className="mono">{t.family}</dd></div>
        </dl>
      </details>
    </div>
  );
}
