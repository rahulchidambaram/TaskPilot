import { useEffect, useRef, useState } from "react";
import { api, API, ACTIVE } from "../api.js";
import { StatusBadge } from "../components/Common.jsx";
import Result from "../components/Result.jsx";

const ICON = { ok: "✓", error: "!", blocked: "⛔", denied: "✋", invalid: "?", info: "i" };
const STATUS_TEXT = { ok: "Done", error: "Error", blocked: "Blocked", denied: "Declined", invalid: "Invalid" };

function Step({ s }) {
  const [big, setBig] = useState(false);
  const src = s.screenshot ? API + s.screenshot : null;
  return (
    <li className={`step s-${s.status}`}>
      <div className="step-n" aria-hidden="true">{ICON[s.status] || s.n}</div>
      <div className="step-main">
        <div className="step-head">
          <b>{s.n}. {s.summary}</b>
          <span className={`chip st-${s.status}`}>{STATUS_TEXT[s.status] || s.status}</span>
          {s.risk !== "low" && <span className={`chip risk-${s.risk}`}>{s.risk} risk</span>}
          {s.approval === "approved" && <span className="chip st-ok">you approved</span>}
          {s.approval === "auto" && <span className="chip">auto-approved</span>}
        </div>
        {s.thought && <p className="thought">“{s.thought}”</p>}
        <p className={`result ${s.status !== "ok" ? "bad" : ""}`}>{s.result}</p>
        {s.risk_reason && s.status === "denied" && <p className="reason">Why approval was needed: {s.risk_reason}</p>}
        {src && (
          <button className={`shot ${big ? "big" : ""}`} onClick={() => setBig(!big)} aria-label={big ? "Shrink screenshot" : "Enlarge screenshot"}>
            <img src={src} alt={`Browser after step ${s.n}`} loading="lazy" />
          </button>
        )}
      </div>
    </li>
  );
}

export default function RunView({ id }) {
  const [run, setRun] = useState(null);
  const [steps, setSteps] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const after = useRef(0);
  const bottom = useRef(null);

  useEffect(() => {
    let alive = true;
    let timer;
    after.current = 0;
    setSteps([]); setRun(null); setError("");
    const tick = async () => {
      try {
        const r = await api.getRun(id);               // status first ...
        const fresh = await api.getSteps(id, after.current); // ... then steps, so a terminal status always has all steps
        if (!alive) return;
        if (fresh.length) {
          after.current = fresh[fresh.length - 1].n;
          setSteps((prev) => [...prev, ...fresh]);
        }
        setRun(r);
        if (ACTIVE.has(r.status)) timer = setTimeout(tick, 900);
      } catch (e) {
        if (!alive) return;
        setError(e.message);
        if (e.status !== 404) timer = setTimeout(tick, 3000);
      }
    };
    tick();
    return () => { alive = false; clearTimeout(timer); };
  }, [id]);

  useEffect(() => { if (run && ACTIVE.has(run.status)) bottom.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [steps.length, run?.status]);

  const act = async (fn) => {
    setBusy(true);
    try { await fn(); } catch (e) { setError(e.message); }
    setBusy(false);
  };

  if (error && !run) return <div className="banner err">{error} <a href="#/history">Back to history</a></div>;
  if (!run) return <p className="muted">Loading run...</p>;
  const active = ACTIVE.has(run.status);
  const p = run.pending;

  return (
    <>
      <section className="run-head">
        <div>
          <a href="#/" className="back">← New task</a>
          <h1>{run.title}</h1>
          <p className="muted">
            {run.mode === "agent" ? "AI agent" : "Playbook"} · {steps.length} step{steps.length === 1 ? "" : "s"}
            {run.params?.llm ? ` · ${run.params.llm}` : ""}
            {run.params?.require_approval === false ? " · auto-approve ON" : ""}
          </p>
        </div>
        <div className="actions">
          <StatusBadge status={run.status} />
          {active && <button className="danger" disabled={busy} onClick={() => act(() => api.stop(id))}>Stop</button>}
          {!active && (
            <>
              <button onClick={() => act(() => api.download(id, "json"))}>Export JSON</button>
              <button onClick={() => act(() => api.download(id, "csv"))}>Export CSV</button>
              <button className="danger-outline" onClick={() => act(async () => { await api.remove(id); window.location.hash = "#/history"; })}>Delete</button>
            </>
          )}
        </div>
      </section>

      {error && <div className="banner err">{error}</div>}

      {run.status === "awaiting_approval" && p && (
        <section className="approval" role="alertdialog" aria-labelledby="ap-title">
          <h3 id="ap-title">Approval needed</h3>
          <p className="ap-action">{p.summary}</p>
          <p className="muted">{p.reason}. Page: {p.url}</p>
          <div className="actions">
            <button className="primary" disabled={busy} onClick={() => act(() => api.approve(id, true))}>Approve</button>
            <button disabled={busy} onClick={() => act(() => api.approve(id, false))}>Decline</button>
          </div>
          <p className="muted small">If you don't answer, the action is declined automatically.</p>
        </section>
      )}

      {run.status === "failed" && run.error && <div className="banner err" role="alert"><b>Run failed:</b> {run.error}</div>}
      {run.status === "stopped" && <div className="banner info">Run stopped.</div>}

      <Result run={run} />

      <section className="card">
        <h3>Activity {active && <span className="pulse" aria-label="live" />}</h3>
        {steps.length === 0 ? <p className="muted">{run.status === "queued" ? "Waiting for a free browser..." : "Starting the browser..."}</p> : (
          <ol className="timeline">{steps.map((s) => <Step key={s.n} s={s} />)}</ol>
        )}
        <div ref={bottom} />
      </section>
    </>
  );
}
