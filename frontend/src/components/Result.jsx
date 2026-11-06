import { Table, Collapsible } from "./Common.jsx";

function Audit({ a }) {
  const tone = a.score >= 85 ? "good" : a.score >= 60 ? "mid" : "bad";
  return (
    <div className="audit">
      <div className={`score ${tone}`}><b>{a.score}</b><span>/100</span></div>
      <div className="audit-body">
        <h4>{a.title || a.url}</h4>
        {a.issues.length === 0 ? <p className="ok-text">No issues found.</p> : (
          <ul className="issues">{a.issues.map((i) => <li key={i}>{i}</li>)}</ul>
        )}
        <div className="facts">
          {[["Links", a.links], ["Images", a.images], ["Forms", a.forms], ["Words", a.word_count], ["H2 headings", a.h2_count], ["External scripts", a.external_scripts]].map(([k, v]) => (
            <div key={k}><b>{v}</b><span>{k}</span></div>
          ))}
        </div>
      </div>
    </div>
  );
}

function DataBlock({ k, v }) {
  if (k === "audit" && v && typeof v === "object" && "score" in v) return <Audit a={v} />;
  if (Array.isArray(v) && v.length && typeof v[0] === "object") return <Table rows={v} />;
  if (v && typeof v === "object" && Array.isArray(v.results)) {
    return (
      <>
        <p>
          Checked <b>{v.checked}</b> links, <b className={v.problems ? "bad-text" : "ok-text"}>{v.problems}</b> problem(s).
        </p>
        <Table rows={v.results.map((r) => ({ url: r.url, status: r.status, ok: r.ok, ms: r.ms, note: r.note }))} />
      </>
    );
  }
  if (typeof v === "string") return <p className="prewrap">{v}</p>;
  return <pre>{JSON.stringify(v, null, 2)}</pre>;
}

export default function Result({ run }) {
  const res = run.result;
  if (!res) return null;
  const data = res.data || {};
  const keys = Object.keys(data);
  const answer = res.answer;
  if (answer === undefined && keys.length === 0) return null;
  return (
    <section className="card">
      <h3>Result</h3>
      {answer !== undefined && answer !== "" && (
        <div className={`answer ${res.success === false ? "partial" : ""}`}>
          {typeof answer === "string" ? <p className="prewrap">{answer}</p> : <pre>{JSON.stringify(answer, null, 2)}</pre>}
        </div>
      )}
      {keys.map((k) => (
        <div key={k} className="datablock">
          {keys.length > 1 || answer !== undefined ? <h4>{k}</h4> : null}
          <DataBlock k={k} v={data[k]} />
        </div>
      ))}
      <Collapsible title="raw JSON"><pre>{JSON.stringify(res, null, 2)}</pre></Collapsible>
    </section>
  );
}
