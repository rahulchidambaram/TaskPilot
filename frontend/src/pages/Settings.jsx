import { useState } from "react";
import { getSettings, saveSettings } from "../api.js";

export default function Settings({ config }) {
  const [s, setS] = useState(getSettings());
  const [saved, setSaved] = useState(false);
  const set = (k) => (e) => { setS({ ...s, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }); setSaved(false); };
  const save = (e) => {
    e.preventDefault();
    saveSettings(s);
    window.dispatchEvent(new Event("tp-settings"));
    setSaved(true);
  };
  const keyLink = s.provider === "groq" ? "https://console.groq.com/keys" : "https://aistudio.google.com/apikey";
  return (
    <>
      <h1>Settings</h1>
      <form className="card form" onSubmit={save}>
        <h3>LLM for the AI agent</h3>
        <p className="muted">
          Server default: {config?.llm_configured ? `${config.llm_provider} (${config.llm_model})` : "none configured"}.
          Add your own free key to use your own quota. It stays in this browser.
        </p>
        <div className="grid2">
          <div>
            <label htmlFor="prov">Provider</label>
            <select id="prov" value={s.provider} onChange={set("provider")}>
              <option value="gemini">Google Gemini (free tier)</option>
              <option value="groq">Groq (free tier)</option>
            </select>
          </div>
          <div>
            <label htmlFor="model">Model <span className="muted">(optional)</span></label>
            <input id="model" value={s.model} onChange={set("model")} placeholder="leave blank for the default" />
          </div>
        </div>
        <label htmlFor="key">API key</label>
        <input id="key" type="password" autoComplete="off" value={s.apiKey} onChange={set("apiKey")} placeholder="paste your key" />
        <p className="muted small">Get a free key: <a href={keyLink} target="_blank" rel="noopener noreferrer">{keyLink.replace("https://", "")}</a></p>

        <h3>Safety defaults</h3>
        <label className="check"><input type="checkbox" checked={s.requireApproval} onChange={set("requireApproval")} /> Ask for approval before state-changing actions</label>

        {config?.access_required && (
          <>
            <h3>Access</h3>
            <label htmlFor="code">Access code</label>
            <input id="code" type="password" autoComplete="off" value={s.accessCode} onChange={set("accessCode")} />
          </>
        )}
        <div className="actions">
          <button className="primary">Save settings</button>
          {saved && <span className="ok-text" role="status">Saved</span>}
        </div>
      </form>
    </>
  );
}
