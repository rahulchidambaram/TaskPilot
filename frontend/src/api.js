// Tiny API client. Auth model: an anonymous per-browser client id (history is private to this browser)
// plus an optional shared access code, and an optional bring-your-own LLM key kept only in localStorage.

export const API = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");

const read = (k, fallback) => {
  try { return JSON.parse(localStorage.getItem(k)) ?? fallback; } catch { return fallback; }
};
const write = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } };

export function clientId() {
  let id = read("tp_client_id", null);
  if (!id) {
    id = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now());
    write("tp_client_id", id);
  }
  return id;
}

export const defaultSettings = { accessCode: "", provider: "gemini", apiKey: "", model: "", requireApproval: true };
export const getSettings = () => ({ ...defaultSettings, ...read("tp_settings", {}) });
export const saveSettings = (s) => write("tp_settings", s);

function headers(extra = {}) {
  const s = getSettings();
  const h = { "X-Client-Id": clientId(), ...extra };
  if (s.accessCode) h["X-Access-Code"] = s.accessCode;
  return h;
}

async function request(path, { method = "GET", body, raw = false } = {}) {
  let res;
  try {
    res = await fetch(API + path, {
      method,
      headers: headers(body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("Cannot reach the TaskPilot server. If it is hosted on a free tier it may be waking up; retry in about a minute.");
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* not json */ }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  if (raw) return res;
  return res.status === 204 ? null : res.json();
}

export const api = {
  config: () => request("/api/config"),
  listRuns: () => request("/api/runs"),
  getRun: (id) => request(`/api/runs/${id}`),
  getSteps: (id, after = 0) => request(`/api/runs/${id}/steps?after=${after}`),
  createRun: (body) => {
    const s = getSettings();
    const payload = { ...body };
    if (s.apiKey) payload.llm = { provider: s.provider, api_key: s.apiKey, ...(s.model ? { model: s.model } : {}) };
    if (payload.require_approval === undefined) payload.require_approval = s.requireApproval;
    return request("/api/runs", { method: "POST", body: payload });
  },
  approve: (id, approve) => request(`/api/runs/${id}/approve`, { method: "POST", body: { approve } }),
  stop: (id) => request(`/api/runs/${id}/stop`, { method: "POST" }),
  remove: (id) => request(`/api/runs/${id}`, { method: "DELETE" }),
  download: async (id, format) => {
    const res = await request(`/api/runs/${id}/export?format=${format}`, { raw: true });
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: `taskpilot-${id.slice(0, 8)}.${format}` });
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};

export const ACTIVE = new Set(["queued", "running", "awaiting_approval"]);
