import { useState } from "react";
import { api, API, getSettings } from "../api.js";

const demoBase = () => (API || window.location.origin) + "/demo/";

const EXAMPLES = () => [
  { label: "Find a price", goal: "Find the price of the Travel Mug in the Demo Store and tell me what it costs.", url: demoBase() },
  { label: "Compare in a category", goal: "In the Demo Store, find the cheapest Electronics product and report its name and price.", url: demoBase() },
  { label: "Needs approval", goal: "Add the Ceramic Mug to the cart in the Demo Store.", url: demoBase() },
  { label: "Try to buy (blocked)", goal: "Buy the Desk Lamp from the Demo Store. Go all the way through checkout and pay.", url: demoBase() },
  { label: "Research on the web", goal: "Find out who created the Python programming language and in what year it was first released.", url: "https://en.wikipedia.org/wiki/Python_(programming_language)" },
];

function AgentForm({ config }) {
  const s = getSettings();
  const [goal, setGoal] = useState("");
  const [url, setUrl] = useState("");
  const [domains, setDomains] = useState("");
  const [maxSteps, setMaxSteps] = useState(10);
  const [approval, setApproval] = useState(s.requireApproval);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const hasLLM = (config && config.llm_configured) || !!s.apiKey;

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const run = await api.createRun({
        mode: "agent", goal, start_url: url || null, max_steps: Number(maxSteps), require_approval: approval,
        allowed_domains: domains.split(",").map((d) => d.trim()).filter(Boolean),
      });
      window.location.hash = `#/run/${run.id}`;
    } catch (err) { setError(err.message); setBusy(false); }
  };

  return (
    <form onSubmit={submit} className="form">
      {!hasLLM && config && (
        <div className="banner info">
          The AI agent needs an LLM key. Add a free Gemini or Groq key in <a href="#/settings">Settings</a> (stored only in your browser),
          or try a <b>Playbook</b> which needs no key.
        </div>
      )}
      <label htmlFor="goal">What should the agent do?</label>
      <textarea id="goal" rows={3} required minLength={5} maxLength={1000} value={goal} onChange={(e) => setGoal(e.target.value)}
        placeholder="Describe a low-risk task in plain English, e.g. 'Find the price of the Travel Mug in the Demo Store'" />
      <div className="chips" aria-label="Examples">
        {EXAMPLES().map((ex) => (
          <button type="button" key={ex.label} className="chip" onClick={() => { setGoal(ex.goal); setUrl(ex.url); }}>{ex.label}</button>
        ))}
      </div>
      <div className="grid2">
        <div>
          <label htmlFor="url">Start URL <span className="muted">(optional)</span></label>
          <input id="url" type="text" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://..." />
        </div>
        <div>
          <label htmlFor="domains">Allowed domains <span className="muted">(optional, comma separated)</span></label>
          <input id="domains" value={domains} onChange={(e) => setDomains(e.target.value)} placeholder="wikipedia.org, example.com" />
        </div>
      </div>
      <div className="grid2">
        <div>
          <label htmlFor="steps">Max steps: {maxSteps}</label>
          <input id="steps" type="range" min="3" max={Math.min(30, config?.max_steps ? Math.max(config.max_steps, 15) : 20)} value={maxSteps} onChange={(e) => setMaxSteps(e.target.value)} />
        </div>
        <label className="check">
          <input type="checkbox" checked={approval} onChange={(e) => setApproval(e.target.checked)} />
          Ask me before state-changing actions (recommended)
        </label>
      </div>
      {error && <div className="banner err" role="alert">{error}</div>}
      <button className="primary" disabled={busy || !goal.trim()}>{busy ? "Starting..." : "Run agent"}</button>
    </form>
  );
}

function PlaybookCard({ pb }) {
  const [vals, setVals] = useState(() => Object.fromEntries(pb.params.map((p) => [p.key, p.default])));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async () => {
    setBusy(true); setError("");
    try {
      const params = { ...vals };
      pb.params.forEach((p) => { if (p.type === "number") params[p.key] = Number(params[p.key]); });
      const r = await api.createRun({ mode: "playbook", playbook_id: pb.id, params });
      window.location.hash = `#/run/${r.id}`;
    } catch (err) { setError(err.message); setBusy(false); }
  };
  return (
    <div className="card pb">
      <span className="tag">{pb.category}</span>
      <h3>{pb.name}</h3>
      <p>{pb.description}</p>
      {pb.params.map((p) => (
        <div key={p.key}>
          <label htmlFor={`${pb.id}-${p.key}`}>{p.label}</label>
          <input id={`${pb.id}-${p.key}`} type={p.type === "number" ? "number" : "text"} value={vals[p.key]}
            onChange={(e) => setVals({ ...vals, [p.key]: e.target.value })} />
        </div>
      ))}
      {error && <div className="banner err" role="alert">{error}</div>}
      <button className="primary" onClick={run} disabled={busy}>{busy ? "Starting..." : "Run playbook"}</button>
    </div>
  );
}

export default function NewTask({ config }) {
  const [tab, setTab] = useState("agent");
  const playbooks = config?.playbooks || [];
  const groups = [...new Set(playbooks.map((p) => p.category))];
  return (
    <>
      <section className="hero">
        <h1>Automate multi-step browser tasks, safely.</h1>
        <p>
          Describe a task and TaskPilot drives a real browser to do it. Reading, searching and extracting run freely;
          anything that changes state asks you first; payments, passwords and deletions are blocked outright.
        </p>
      </section>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "agent"} className={tab === "agent" ? "on" : ""} onClick={() => setTab("agent")}>AI agent</button>
        <button role="tab" aria-selected={tab === "playbooks"} className={tab === "playbooks" ? "on" : ""} onClick={() => setTab("playbooks")}>Playbooks <span className="muted">(no LLM key needed)</span></button>
      </div>
      {tab === "agent" ? (
        <section className="card"><AgentForm config={config} /></section>
      ) : (
        groups.map((g) => (
          <section key={g}>
            <h2 className="group">{g}</h2>
            <div className="pb-grid">{playbooks.filter((p) => p.category === g).map((p) => <PlaybookCard key={p.id} pb={p} />)}</div>
          </section>
        ))
      )}
      {tab === "playbooks" && !playbooks.length && <p className="muted">Loading playbooks...</p>}
    </>
  );
}
