// The List's instrument filter (multi-instrument UI spec 2026-11-02, section 2): checkboxes grouped Gold·Metals·
// Equity·FX·Energy·Rates·Ags, each group with a header checkbox. The choices are the server's facet (instruments with
// a trade under the other filters) plus whatever the URL already picks, so the panel never offers an empty choice and
// never hides an active one. Picking a group header selects the whole group (grp=); picking a symbol, that symbol
// (sym=); unticking one symbol of a picked group keeps the rest of the group picked.
import { useEffect, useRef } from "react";
import { GROUP_ORDER, GROUP_WORDS, groupOf, type InstrumentGroup, labelOf } from "../../lib/instruments";
import type { Filters } from "../../lib/trades";

type SetFilters = (n: Partial<Filters>) => void;

function HeaderBox({ g, checked, mixed, onChange }: { g: InstrumentGroup; checked: boolean; mixed: boolean; onChange: () => void }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { if (ref.current) ref.current.indeterminate = mixed; }, [mixed]);
  return (
    <label className="sym-group-head">
      <input ref={ref} type="checkbox" checked={checked} onChange={onChange} />
      <strong>{GROUP_WORDS[g]}</strong>
    </label>
  );
}

export function SymbolFilter({ f, set, symbols }: { f: Filters; set: SetFilters; symbols: readonly string[] }) {
  const all = [...new Set([...symbols, ...f.sym])];
  const groups = GROUP_ORDER.map((g) => [g, all.filter((s) => groupOf(s) === g).sort()] as const).filter(([g, xs]) => xs.length > 0 || f.grp.includes(g));
  if (groups.length === 0) return null;
  const toggleGroup = (g: InstrumentGroup, members: readonly string[]) => {
    if (f.grp.includes(g)) set({ grp: f.grp.filter((x) => x !== g) });
    else set({ grp: [...f.grp, g], sym: f.sym.filter((s) => !members.includes(s)) });
  };
  const toggleSym = (s: string, g: InstrumentGroup, members: readonly string[]) => {
    if (f.grp.includes(g)) {                 // the group was picked: keep its other members
      set({ grp: f.grp.filter((x) => x !== g), sym: [...f.sym, ...members.filter((m) => m !== s && !f.sym.includes(m))] });
    } else set({ sym: f.sym.includes(s) ? f.sym.filter((x) => x !== s) : [...f.sym, s] });
  };
  return (
    <fieldset className="sym-filter"><legend>Instrument</legend>
      {groups.map(([g, members]) => {
        const picked = f.grp.includes(g);
        const some = !picked && members.some((s) => f.sym.includes(s));
        return (
          <div key={g} className="sym-group" role="group" aria-label={`${GROUP_WORDS[g]} instruments`}>
            <HeaderBox g={g} checked={picked} mixed={some} onChange={() => toggleGroup(g, members)} />
            <div className="sym-group-items">
              {members.map((s) => (
                <label key={s} className="sym-item" title={labelOf(s)}>
                  <input type="checkbox" checked={picked || f.sym.includes(s)} onChange={() => toggleSym(s, g, members)} />
                  {s} <span className="muted small">{labelOf(s) !== s ? labelOf(s) : ""}</span>
                </label>
              ))}
            </div>
          </div>
        );
      })}
    </fieldset>
  );
}
