// Lightweight inline-SVG charts for the Health screen (docs/ux/health-research.md). Each chart has a title, an
// aria description, a native hover title on every mark and a "Show as table" alternative; colours come from the
// theme tokens in styles.css, so light and dark both hold their contrast. State is never colour alone.
import { useId, useState, type ReactNode } from "react";
import {
  PSI_WORDS, ddState, linePath, localDay, localTime, num, pct, psiBand, scale, utcTitle,
  type AgentHealthRow, type CusumTrace, type PsiHeatmap as PsiData, type ReliabilityCurve,
} from "../lib/explain";

function AsTable({ children }: { children: ReactNode }) {
  return (
    <details className="as-table">
      <summary>Show as table</summary>
      <div className="scroll">{children}</div>
    </details>
  );
}

// ------------------------------------------------------------------ PSI heatmap
export function PsiHeatmap({ data }: { data: PsiData }) {
  if (data.features.length === 0) {
    return <p className="empty muted">No PSI yet. It needs a live champion with at least 50 recent candidates and a training reference.</p>;
  }
  return (
    <figure className="chart">
      <div className="scroll">
        <table className="heatmap" aria-describedby="psi-legend">
          <caption className="sr-only">PSI per feature (rows) and agent (columns)</caption>
          <thead><tr><th scope="col">Feature</th>{data.agents.map((a) => <th scope="col" key={a}>{a}</th>)}</tr></thead>
          <tbody>
            {data.features.map((f, i) => (
              <tr key={f}>
                <th scope="row" className="mono">{f}</th>
                {data.values[i].map((v, j) => {
                  if (v == null) return <td key={j} className="psi none" title={`${data.agents[j]} does not use ${f}`}>·</td>;
                  const band = psiBand(v, data.warn, data.size_down);
                  return (
                    <td key={j} className={`psi ${band}`} title={`${data.agents[j]} · ${f}: PSI ${v.toFixed(3)} (${PSI_WORDS[band]})`}>
                      {v.toFixed(2)}{band !== "ok" && <span className="sr-only"> {PSI_WORDS[band]}</span>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <figcaption id="psi-legend" className="legend">
        <span><i className="swatch ok" /> &lt; {data.warn} stable</span>
        <span><i className="swatch warn" /> {data.warn}–{data.size_down} drifting (warns)</span>
        <span><i className="swatch bad" /> &gt; {data.size_down} sizes the agent down to 50%</span>
      </figcaption>
    </figure>
  );
}

// ------------------------------------------------------------------ reliability curve
const RW = 320, RH = 240, RM = { l: 40, r: 12, t: 12, b: 36 };

export function ReliabilityChart({ curves }: { curves: ReliabilityCurve[] }) {
  const [pick, setPick] = useState(0);
  const id = useId();
  if (curves.length === 0) {
    return <p className="empty muted">No closed shadow trades yet. The curve fills in as the shadow book records outcomes.</p>;
  }
  const c = curves[Math.min(pick, curves.length - 1)];
  const x = scale(0, 1, RM.l, RW - RM.r), y = scale(0, 1, RH - RM.b, RM.t);
  const pts = c.bins.map((b) => [x(b.mean_p), y(b.hit_rate)] as [number, number]);
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const label = c.agent_id === "all" ? "All agents" : c.agent_id;
  return (
    <figure className="chart">
      {curves.length > 1 && (
        <label className="chart-pick">Agent{" "}
          <select value={pick} onChange={(e) => setPick(Number(e.target.value))}>
            {curves.map((k, i) => <option key={k.agent_id} value={i}>{k.agent_id === "all" ? "All agents" : k.agent_id}</option>)}
          </select>
        </label>
      )}
      <svg viewBox={`0 0 ${RW} ${RH}`} role="img" aria-labelledby={`${id}-t`} className="svg-chart">
        <title id={`${id}-t`}>{`Reliability of ${label}: predicted probability against realised hit rate over ${c.n} trades`}</title>
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={x(0)} x2={x(1)} y1={y(t)} y2={y(t)} />
            <text className="tick" x={RM.l - 6} y={y(t) + 4} textAnchor="end">{t}</text>
            <text className="tick" x={x(t)} y={RH - RM.b + 16} textAnchor="middle">{t}</text>
          </g>
        ))}
        <text className="axis" x={(RM.l + RW - RM.r) / 2} y={RH - 4} textAnchor="middle">predicted p</text>
        <text className="axis" transform={`translate(11 ${(RM.t + RH - RM.b) / 2}) rotate(-90)`} textAnchor="middle">hit rate</text>
        <line className="ideal" x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)}><title>perfect calibration</title></line>
        <path className="series" d={linePath(pts)} />
        {c.bins.map((b, i) => (
          <g key={b.lo}>
            <circle className="dot" cx={pts[i][0]} cy={pts[i][1]} r={5}>
              <title>{`p ${b.lo.toFixed(1)}–${b.hi.toFixed(1)}: predicted ${b.mean_p.toFixed(2)}, hit ${pct(b.hit_rate, 0)} of ${b.n} trades`}</title>
            </circle>
            <text className="count" x={pts[i][0] + 9} y={pts[i][1] + 4}>{b.n}</text>
          </g>
        ))}
      </svg>
      <figcaption className="muted small">
        {label}: {c.n} trades · ECE {num(c.ece, 3)} (sizes down above 0.08) · Brier {num(c.brier, 3)}. Numbers beside the dots are
        trade counts; the dashed line is perfect calibration.
      </figcaption>
      <AsTable>
        <table>
          <thead><tr><th>p bin</th><th>Trades</th><th>Mean predicted p</th><th>Realised hit rate</th></tr></thead>
          <tbody>{c.bins.map((b) => <tr key={b.lo}><td>{b.lo.toFixed(1)}–{b.hi.toFixed(1)}</td><td>{b.n}</td><td>{b.mean_p.toFixed(2)}</td><td>{pct(b.hit_rate, 0)}</td></tr>)}</tbody>
        </table>
      </AsTable>
    </figure>
  );
}

// ------------------------------------------------------------------ CUSUM trace
const CW = 320, CH = 140, CM = { l: 32, r: 12, t: 12, b: 28 };

export function CusumChart({ trace }: { trace: CusumTrace }) {
  const id = useId();
  const n = trace.points.length;
  const top = Math.max(trace.h * 1.15, ...trace.points.map((p) => p.s));
  const x = scale(0, Math.max(n - 1, 1), CM.l, CW - CM.r), y = scale(0, top, CH - CM.b, CM.t);
  const pts = trace.points.map((p, i) => [x(i), y(p.s)] as [number, number]);
  const last = trace.points[n - 1];
  return (
    <figure className={`chart cusum ${trace.alarm ? "alarm" : ""}`}>
      <figcaption className="chart-title">
        <strong>{trace.agent_id}</strong> <span className="muted">{trace.version}</span>{" "}
        {trace.alarm ? <span className="state bad">alarm: halted</span> : <span className="state ok">below threshold</span>}
      </figcaption>
      <svg viewBox={`0 0 ${CW} ${CH}`} role="img" aria-labelledby={`${id}-t`} className="svg-chart">
        <title id={`${id}-t`}>{`CUSUM of ${trace.agent_id} over ${n} trades: now ${last ? last.s.toFixed(2) : 0}, halts above ${trace.h}`}</title>
        <line className="grid" x1={CM.l} x2={CW - CM.r} y1={y(0)} y2={y(0)} />
        <text className="tick" x={CM.l - 6} y={y(0) + 4} textAnchor="end">0</text>
        <line className="limit" x1={CM.l} x2={CW - CM.r} y1={y(trace.h)} y2={y(trace.h)} />
        <text className="tick" x={CM.l - 6} y={y(trace.h) + 4} textAnchor="end">{trace.h}</text>
        <text className="limit-label" x={CW - CM.r} y={y(trace.h) - 4} textAnchor="end">halt at {trace.h}</text>
        <path className="series" d={linePath(pts)} />
        {trace.points.map((p, i) => (
          <circle key={i} className="hit" cx={pts[i][0]} cy={pts[i][1]} r={6}>
            <title>{`trade ${i + 1}, ${utcTitle(p.ts)}: residual ${p.z >= 0 ? "+" : ""}${p.z.toFixed(2)}, CUSUM ${p.s.toFixed(2)}`}</title>
          </circle>
        ))}
        {n > 0 && <>
          <text className="tick" x={CM.l} y={CH - 8}>{localDay(trace.points[0].ts)}</text>
          <text className="tick" x={CW - CM.r} y={CH - 8} textAnchor="end">{localDay(last.ts)}</text>
        </>}
      </svg>
      <AsTable>
        <table>
          <thead><tr><th>Trade</th><th>Exit</th><th>Residual (z)</th><th>CUSUM</th></tr></thead>
          <tbody>{trace.points.map((p, i) => <tr key={i}><td>{i + 1}</td><td title={utcTitle(p.ts)}>{localTime(p.ts)}</td><td>{p.z.toFixed(2)}</td><td>{p.s.toFixed(2)}</td></tr>)}</tbody>
        </table>
      </AsTable>
    </figure>
  );
}

// ------------------------------------------------------------------ 30-day drawdown vs the limit
export function DrawdownBars({ agents, mult }: { agents: AgentHealthRow[]; mult: number }) {
  if (agents.length === 0) return <p className="empty muted">No drawdown yet: no champion has been checked.</p>;
  const rows = agents.map((a) => ({ a, ...ddState(a.dd_30d, a.backtest_dd, mult) }));
  const top = Math.max(0.01, ...rows.map((r) => Math.max(r.a.dd_30d, r.limit ?? 0))) * 1.15;
  return (
    <ul className="ddbars">
      {rows.map(({ a, limit, band }) => (
        <li key={a.agent_id}>
          <div className="ddhead">
            <strong>{a.agent_id}</strong>
            <span className={`state ${band}`}>
              {pct(a.dd_30d)}{limit != null ? ` of ${pct(limit)} limit` : ""}{band === "bad" ? ": system halt" : ""}
            </span>
          </div>
          <div className="ddtrack" role="meter" aria-label={`${a.agent_id} 30-day drawdown`} aria-valuemin={0}
            aria-valuemax={limit ?? top} aria-valuenow={a.dd_30d} aria-valuetext={`${pct(a.dd_30d)}${limit != null ? ` of a ${pct(limit)} limit` : ", no limit known"}`}>
            <span className={`ddfill ${band}`} style={{ width: `${Math.min(100, (a.dd_30d / top) * 100)}%` }} />
            {limit != null && <span className="ddlimit" style={{ left: `${(limit / top) * 100}%` }} title={`limit ${mult}× backtest ${pct(a.backtest_dd ?? 0)}`} />}
          </div>
          {limit == null && <p className="muted small">No backtest drawdown recorded, so no limit to compare against.</p>}
        </li>
      ))}
    </ul>
  );
}
