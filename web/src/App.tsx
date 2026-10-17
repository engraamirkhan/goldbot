import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, currentUser, hasRole, hasToken, liveSocket, setToken } from "./lib/api";
import { roleLabel } from "./lib/roles";
import { Login } from "./screens/Login";
import { Overview } from "./screens/Overview";
import { Approvals } from "./screens/Approvals";
import { Agents } from "./screens/Agents";
import { Feeds } from "./screens/Feeds";
import { Health } from "./screens/Health";
import { Research } from "./screens/Research";
import { News } from "./screens/News";
import { Users } from "./screens/Users";
import { Account } from "./screens/Account";
import { Progress } from "./screens/Progress";

// Progress is third: on a phone the tab row scrolls sideways, so a tab at the end would be off-screen
// (docs/ux/progress-screens.md "Placement")
const TABS = ["Overview", "Approvals", "Progress", "News", "Agents", "Health", "Research", "Feeds", "Users", "Account"] as const;
type Tab = (typeof TABS)[number];

export function App() {
  const [authed, setAuthed] = useState(hasToken());
  const [tab, setTab] = useState<Tab>("Overview");
  const qc = useQueryClient();

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

  if (!authed) return <Login onLogin={() => setAuthed(true)} />;
  const me = currentUser();

  return (
    <div className="shell">
      <header>
        <h1>goldbot</h1>
        <nav>
          {TABS.filter((t) => t !== "Users" || hasRole("owner")).map((t) => (
            <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>{t}</button>
          ))}
        </nav>
        {/* Role, never the email: persistent chrome never renders an identity (docs/ux/header-role.md) */}
        <span className="muted who">{me ? <><span className="sr-only">Signed in as </span>{roleLabel(me.role)}</> : "Signed in"}</span>
        <button className="link" onClick={async () => { try { await api.logout(); } catch { /* session already gone server-side */ } setToken(null); setAuthed(false); }}>Sign out</button>
      </header>
      <main>
        {tab === "Overview" && <Overview onOpenProgress={() => setTab("Progress")} />}
        {tab === "Approvals" && <Approvals />}
        {tab === "Progress" && <Progress onOpenOverview={() => setTab("Overview")} />}
        {tab === "News" && <News />}
        {tab === "Agents" && <Agents />}
        {tab === "Health" && <Health />}
        {tab === "Research" && <Research />}
        {tab === "Feeds" && <Feeds />}
        {tab === "Users" && hasRole("owner") && <Users />}
        {tab === "Account" && <Account />}
      </main>
    </div>
  );
}
