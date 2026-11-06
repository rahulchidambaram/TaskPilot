import { useEffect, useState } from "react";
import { api, ACTIVE } from "../api.js";
import { StatusBadge, timeAgo } from "../components/Common.jsx";

export default function History() {
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true, timer;
    const load = async () => {
      try {
        const r = await api.listRuns();
        if (!alive) return;
        setRuns(r); setError("");
        if (r.some((x) => ACTIVE.has(x.status))) timer = setTimeout(load, 3000);
      } catch (e) { if (alive) setError(e.message); }
    };
    load();
    return () => { alive = false; clearTimeout(timer); };
  }, []);

  const remove = async (id) => {
    await api.remove(id).catch((e) => setError(e.message));
    setRuns((rs) => rs.filter((r) => r.id !== id));
  };

  return (
    <>
      <h1>History</h1>
      <p className="muted">Runs started from this browser. Stored on the server for a limited time; export anything you want to keep.</p>
      {error && <div className="banner err">{error}</div>}
      {runs === null && !error && <p className="muted">Loading...</p>}
      {runs && runs.length === 0 && <div className="card empty">No runs yet. <a href="#/">Start your first task.</a></div>}
      {runs && runs.length > 0 && (
        <ul className="runlist">
          {runs.map((r) => (
            <li key={r.id} className="card row">
              <a href={`#/run/${r.id}`} className="grow">
                <b>{r.title}</b>
                <span className="muted">{r.mode === "agent" ? "AI agent" : "Playbook"} · {r.step_count} steps · {timeAgo(r.created_at)}</span>
              </a>
              <StatusBadge status={r.status} />
              <button className="danger-outline" onClick={() => remove(r.id)} aria-label={`Delete ${r.title}`}>Delete</button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
