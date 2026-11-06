import { useEffect, useState } from "react";
import { api } from "./api.js";
import NewTask from "./pages/NewTask.jsx";
import RunView from "./pages/RunView.jsx";
import History from "./pages/History.jsx";
import Safety from "./pages/Safety.jsx";
import Settings from "./pages/Settings.jsx";

function useHash() {
  const [hash, setHash] = useState(window.location.hash || "#/");
  useEffect(() => {
    const on = () => setHash(window.location.hash || "#/");
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash.replace(/^#/, "") || "/";
}

const NAV = [
  ["/", "New task"],
  ["/history", "History"],
  ["/safety", "Safety"],
  ["/settings", "Settings"],
];

export default function App() {
  const path = useHash();
  const [config, setConfig] = useState(null);
  const [configError, setConfigError] = useState("");

  const loadConfig = () =>
    api.config().then((c) => { setConfig(c); setConfigError(""); }).catch((e) => setConfigError(e.message));
  useEffect(() => { loadConfig(); }, []);
  // Re-check after the user changes settings (e.g. enters an access code).
  useEffect(() => {
    const on = () => loadConfig();
    window.addEventListener("tp-settings", on);
    return () => window.removeEventListener("tp-settings", on);
  }, []);

  const runMatch = path.match(/^\/run\/([0-9a-f]{32})$/);
  let page;
  if (runMatch) page = <RunView id={runMatch[1]} />;
  else if (path === "/history") page = <History />;
  else if (path === "/safety") page = <Safety config={config} />;
  else if (path === "/settings") page = <Settings config={config} />;
  else page = <NewTask config={config} />;

  const section = runMatch ? "/history" : path;
  return (
    <>
      <header className="topbar">
        <a className="brand" href="#/">
          <svg width="26" height="26" viewBox="0 0 64 64" aria-hidden="true"><rect width="64" height="64" rx="14" fill="#14b8a6" /><path d="M14 38 50 14 38 50 31 36z" fill="#042f2e" /></svg>
          TaskPilot
        </a>
        <nav>
          {NAV.map(([to, label]) => (
            <a key={to} href={`#${to}`} className={section === to ? "active" : ""}>{label}</a>
          ))}
        </nav>
      </header>
      <main className="page">
        {configError && <div className="banner warn">{configError} <button className="link" onClick={loadConfig}>Retry</button></div>}
        {config && config.access_required && !config.authorised && (
          <div className="banner warn">This deployment is protected. Enter the access code in <a href="#/settings">Settings</a> to continue.</div>
        )}
        {page}
      </main>
      <footer className="foot">TaskPilot: low-risk browser automation with human-in-the-loop approvals. Capstone project.</footer>
    </>
  );
}
