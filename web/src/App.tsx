import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, currentUser, hasRole, hasToken, liveSocket, setToken } from "./lib/api";
import { Login } from "./screens/Login";
import { Overview } from "./screens/Overview";
import { Approvals } from "./screens/Approvals";
import { Agents } from "./screens/Agents";
import { Feeds } from "./screens/Feeds";
import { News } from "./screens/News";
import { Users } from "./screens/Users";
import { Account } from "./screens/Account";

const TABS = ["Overview", "Approvals", "News", "Agents", "Feeds", "Users", "Account"] as const;
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
    });
  }, [authed, qc]);

  if (!authed) return <Login onLogin={() => setAuthed(true)} />;

  return (
    <div className="shell">
      <header>
        <h1>goldbot</h1>
        <nav>
          {TABS.filter((t) => t !== "Users" || hasRole("owner")).map((t) => (
            <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>{t}</button>
          ))}
        </nav>
        <span className="muted who">{currentUser()?.email} · {currentUser()?.role}</span>
        <button className="link" onClick={async () => { try { await api.logout(); } catch { /* session already gone server-side */ } setToken(null); setAuthed(false); }}>Sign out</button>
      </header>
      <main>
        {tab === "Overview" && <Overview />}
        {tab === "Approvals" && <Approvals />}
        {tab === "News" && <News />}
        {tab === "Agents" && <Agents />}
        {tab === "Feeds" && <Feeds />}
        {tab === "Users" && hasRole("owner") && <Users />}
        {tab === "Account" && <Account />}
      </main>
    </div>
  );
}
