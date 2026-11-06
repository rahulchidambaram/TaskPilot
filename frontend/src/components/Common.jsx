import { useState } from "react";

const LABELS = {
  queued: "Queued", running: "Running", awaiting_approval: "Needs approval",
  completed: "Completed", failed: "Failed", stopped: "Stopped",
};

export function StatusBadge({ status }) {
  return <span className={`badge st-${status}`}>{LABELS[status] || status}</span>;
}

export function timeAgo(ts) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export function Cell({ v }) {
  if (v === null || v === undefined || v === "") return <span className="muted">-</span>;
  if (typeof v === "boolean") return <>{v ? "yes" : "no"}</>;
  if (typeof v === "string" && /^https?:\/\//.test(v))
    return <a href={v} target="_blank" rel="noopener noreferrer">{v.length > 60 ? v.slice(0, 57) + "..." : v}</a>;
  if (typeof v === "object") return <code>{JSON.stringify(v)}</code>;
  return <>{String(v)}</>;
}

export function Table({ rows }) {
  const cols = Object.keys(rows[0] || {});
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{cols.map((c) => <td key={c}><Cell v={r[c]} /></td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Collapsible({ title, children, open = false }) {
  const [o, setO] = useState(open);
  return (
    <div className="collapsible">
      <button className="link" onClick={() => setO(!o)} aria-expanded={o}>{o ? "Hide" : "Show"} {title}</button>
      {o && children}
    </div>
  );
}
