import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { hasToken, liveSocket } from "./lib/api";
import { Login } from "./screens/Login";
import { Overview } from "./screens/Overview";
import { Approvals } from "./screens/Approvals";
import { Agents } from "./screens/Agents";
import { Feeds } from "./screens/Feeds";

const TABS = ["Overview", "Approvals", "Agents", "Feeds"] as const;
type Tab = (typeof TABS)[number];

export function App() {
  const [authed, setAuthed] = useState(hasToken());
  const [tab, setTab] = useState<Tab>("Overview");
  const qc = useQueryClient();

  useEffect(() => {
    if (!authed) return;
    return liveSocket((e) => {
      if (e.type === "decision" || e.type === "proposal") qc.invalidateQueries({ queryKey: ["proposals"] });
    });
  }, [authed, qc]);

  if (!authed) return <Login onLogin={() => setAuthed(true)} />;

  return (
    <div className="shell">
      <header>
        <h1>goldbot</h1>
        <nav>
          {TABS.map((t) => (
            <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>{t}</button>
          ))}
        </nav>
      </header>
      <main>
        {tab === "Overview" && <Overview />}
        {tab === "Approvals" && <Approvals />}
        {tab === "Agents" && <Agents />}
        {tab === "Feeds" && <Feeds />}
      </main>
    </div>
  );
}
