// The Trades screen (docs/design/trades-ux-2026-10-27.md §3, ADR 0050, backlog 128-133): Now (what needs me, what is
// open, with money on every row), List (every trade of every kind, filtered in the URL, one row per trade), Strategies
// (progress to each arm's read, verdicts after it) and Today (per account: fills against the plan, net £ after costs,
// caps, the 14-day strip). One trade opens as a sheet (phone) or a side panel (desktop) at #/trades/<tab>/<trade_id>;
// the browser's back closes it. The old tabs (#/trades/open, activity, summary) redirect (lib/route.ts).
import type { Go, Route } from "../lib/route";
import { TradesList } from "./trades/List";
import { TradesNow } from "./trades/Now";
import { TradesStrategies } from "./trades/Strategies";
import { TradesToday } from "./trades/Today";
import { TradeDetail } from "./trades/TradeDetail";
import { filtersQuery, parseFilters, type TradesTab } from "../lib/trades";
import "./home.css";

export function TradesTabView({ tab, route, go }: { tab: TradesTab; route: Route; go: Go }) {
  const query = route.query ?? "";
  const open = (id: string) => go("trades", tab, id, query, { keepScroll: true });
  const close = () => go("trades", tab, null, query, { keepScroll: true });
  return (
    <>
      {tab === "now" && <TradesNow onOpen={(t) => ("id" in t ? open(t.id) : go("trades", "list", null, t.query))} />}
      {tab === "list" && <TradesList query={query} onOpen={open}
                                     onQuery={(q) => go("trades", "list", null, q, { replace: true, keepScroll: true })} />}
      {tab === "strategies" && <TradesStrategies onFamily={(fam) => go("trades", "list", null, filtersQuery({ ...parseFilters(""), fam: [fam] }))} />}
      {tab === "today" && <TradesToday />}
      {route.sub && (tab === "now" || tab === "list") && <TradeDetail id={route.sub} onClose={close} />}
    </>
  );
}
