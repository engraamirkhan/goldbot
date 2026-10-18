import { useEffect, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { clearLinkTokens } from "./lib/links";
import { api, currentUser, hasRole, hasToken, liveSocket, onSessionEnded, setToken } from "./lib/api";
import { roleLabel } from "./lib/roles";
import { useRoute, type Section } from "./lib/route";
import { Segmented } from "./ui";
import { StatusPill } from "./shell/StatusPill";
import { Login } from "./screens/Login";
import { Home } from "./screens/Home";
import { Approvals } from "./screens/Approvals";
import { Agents } from "./screens/Agents";
import { Feeds } from "./screens/Feeds";
import { Health } from "./screens/Health";
import { Research } from "./screens/Research";
import { News } from "./screens/News";
import { Users } from "./screens/Users";
import { Account } from "./screens/Account";
import { Chart } from "./screens/Chart";
import { Progress } from "./screens/Progress";
import { Limits } from "./screens/Limits";
import { AutoModeCard } from "./screens/AutoMode";
import { HaltControl } from "./screens/HaltControl";
import { CodeSignInsBanner } from "./screens/CodeSignInsBanner";

// Seven destinations (docs/ux/dashboard-v2.md section 2). Phone: bottom bar with the first four and More; desktop: rail.
const NAV: { id: Section; label: string; icon: string }[] = [
  { id: "home", label: "Home", icon: "M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z" },
  { id: "trades", label: "Trades", icon: "M4 17l5-5 4 4 7-8M15 8h5v5" },
  { id: "chart", label: "Chart", icon: "M4 4v16h16M8 14v3M12 10v7M16 6v11" },
  { id: "limits", label: "Limits", icon: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" },
  { id: "research", label: "Research", icon: "M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3M8 3h8" },
  { id: "feeds", label: "Feeds", icon: "M4 11a9 9 0 0 1 9 9M4 4a16 16 0 0 1 16 16M5 19h.01" },
  { id: "settings", label: "Settings", icon: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12l2-1-2-4-2 1-2-1-1-2h-4l-1 2-2 1-2-1-2 4 2 1v0l-2 1 2 4 2-1 2 1 1 2h4l1-2 2-1 2 1 2-4z" },
];
const PHONE_BAR: Section[] = ["home", "trades", "chart", "limits"];

type Tab = { id: string; label: string; owner?: boolean; render: () => ReactNode };

function Icon({ d }: { d: string }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>;
}

function SafetySettings() {
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });
  return (
    <>
      <AutoModeCard />
      <section className="ui-card" aria-label="Halt">
        <h2>Halt new entries</h2>
        <p className="muted small">Stops new entries on every engine. Open trades keep their stop, target and automatic exits.
          Re-arming needs the owner's authenticator code.</p>
        {status.data ? <HaltControl status={status.data} /> : <p className="muted small">Loading…</p>}
      </section>
    </>
  );
}

export function App() {
  const [authed, setAuthed] = useState(hasToken());
  const [route, go] = useRoute();
  const [more, setMore] = useState(false);
  const qc = useQueryClient();

  useEffect(() => onSessionEnded(() => { clearLinkTokens(); setAuthed(false); }), []);

  useEffect(() => {
    if (!authed) return;
    return liveSocket((e) => {
      if (e.type === "decision" || e.type === "proposal") {
        qc.invalidateQueries({ queryKey: ["proposals"] });
        qc.invalidateQueries({ queryKey: ["proposals-recent"] });
      }
      if (e.type === "halt") qc.invalidateQueries({ queryKey: ["status"] });
      if (e.type === "mode") { qc.invalidateQueries({ queryKey: ["automode"] }); qc.invalidateQueries({ queryKey: ["status"] }); }
    });
  }, [authed, qc]);

  if (!authed) return <Login onLogin={() => { clearLinkTokens(); setAuthed(true); }} />;
  const me = currentUser();
  const signOut = async () => {
    try { await api.logout(); } catch { /* session already gone server-side */ }
    setToken(null); clearLinkTokens(); setAuthed(false);
  };

  const TABS: Partial<Record<Section, Tab[]>> = {
    research: [
      { id: "trials", label: "Today and trials", render: () => <Research /> },
      { id: "road", label: "Road to live", render: () => <Progress onOpenOverview={() => go("settings", "safety")} /> },
      { id: "strategies", label: "Strategies", render: () => <Agents /> },
    ],
    feeds: [
      { id: "data", label: "Data feeds", render: () => <Feeds /> },
      { id: "news", label: "News", render: () => <News /> },
      { id: "health", label: "System health", render: () => <Health /> },
    ],
    settings: [
      { id: "safety", label: "Entry mode and halt", render: () => <SafetySettings /> },
      { id: "account", label: "Your sign-in", render: () => <Account /> },
      { id: "users", label: "Users", owner: true, render: () => <Users /> },
    ],
  };
  const SINGLE: Record<string, () => ReactNode> = {
    home: () => <Home onOpenProgress={() => go("research", "road")} onOpenLimits={() => go("limits")} />,
    trades: () => <Approvals />,
    chart: () => <Chart />,
    limits: () => <Limits />,
  };
  const tabs = (TABS[route.section] ?? []).filter((t) => !t.owner || hasRole("owner"));
  const tab = tabs.find((t) => t.id === route.tab) ?? tabs[0];
  const title = NAV.find((n) => n.id === route.section)!.label;
  const link = (n: (typeof NAV)[number], extra?: () => void) => (
    <button key={n.id} type="button" className="navlink" data-nav-item={n.label}
            aria-current={route.section === n.id ? "page" : undefined}
            onClick={() => { extra?.(); go(n.id); }}>
      <Icon d={n.icon} /><span>{n.label}</span>
    </button>
  );

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-row">
          <h1 className="brand">goldbot</h1>
          <StatusPill onOpenTrades={() => go("trades")} />
          {/* Role, never the email: persistent chrome never renders an identity (docs/ux/header-role.md) */}
          <span className="who">{me ? <><span className="sr-only">Signed in as </span>{roleLabel(me.role)}</> : "Signed in"}</span>
          <button type="button" className="signout" onClick={signOut}>Sign out</button>
        </div>
      </header>
      <div className="status-row"><StatusPill onOpenTrades={() => go("trades")} /></div>
      <div className="layout">
        <nav className="rail" aria-label="Main">{NAV.map((n) => link(n))}</nav>
        <div className="content">
          <main>
            {hasRole("owner") && <CodeSignInsBanner onSignedOut={() => { clearLinkTokens(); setAuthed(false); }} />}
            {route.section !== "home" && <div className="page-head"><h1>{title}</h1></div>}
            {tab ? (
              <>
                {tabs.length > 1 && (
                  <Segmented label={title} items={tabs.map((t) => ({ id: t.id, label: t.label }))} value={tab.id}
                             onChange={(id) => go(route.section, id)} />
                )}
                <div role="tabpanel" aria-label={tab.label}>{tab.render()}</div>
              </>
            ) : SINGLE[route.section]?.()}
          </main>
        </div>
      </div>
      {more && (
        <div className="more-sheet" role="group" aria-label="More">
          {NAV.filter((n) => !PHONE_BAR.includes(n.id)).map((n) => link(n, () => setMore(false)))}
        </div>
      )}
      <nav className="bottombar" aria-label="Main, phone">
        {NAV.filter((n) => PHONE_BAR.includes(n.id)).map((n) => link(n, () => setMore(false)))}
        <button type="button" className="navlink" aria-expanded={more} aria-current={!PHONE_BAR.includes(route.section) ? "page" : undefined}
                onClick={() => setMore(!more)}>
          <Icon d="M5 12h.01M12 12h.01M19 12h.01" /><span>More</span>
        </button>
      </nav>
    </div>
  );
}
