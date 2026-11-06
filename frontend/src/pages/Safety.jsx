export default function Safety({ config }) {
  const tiers = [
    { cls: "low", title: "Runs immediately", items: ["Opening pages and following links", "Scrolling, going back, reading page text", "Typing into search boxes and ordinary form fields", "Extracting lists, text and tables"] },
    { cls: "medium", title: "Pauses for your approval", items: ["Submitting forms (POST) and contact messages", "Add to cart, sign up, subscribe, publish, comment", "Proceeding to checkout"] },
    { cls: "blocked", title: "Blocked, no override", items: ["Paying, buying, placing orders, donating, transferring money", "Deleting or deactivating anything", "Passwords, card numbers, CVV, OTPs, government IDs", "Sign-in / authentication forms"] },
  ];
  return (
    <>
      <h1>How TaskPilot stays low-risk</h1>
      <p className="muted">
        These rules run in the server, outside the AI model, so a confused or manipulated model cannot talk its way past them.
      </p>
      <div className="tiers">
        {tiers.map((t) => (
          <section key={t.cls} className={`card tier t-${t.cls}`}>
            <h3>{t.title}</h3>
            <ul>{t.items.map((i) => <li key={i}>{i}</li>)}</ul>
          </section>
        ))}
      </div>
      <section className="card">
        <h3>Network guard</h3>
        <ul>
          <li>Only <code>http</code>/<code>https</code> on standard ports; no embedded credentials in URLs.</li>
          <li>Private, loopback and cloud-metadata addresses are refused (SSRF protection), checked on every request the page makes.</li>
          <li>Optional per-task domain allowlist, e.g. <code>wikipedia.org</code>.</li>
          <li>Downloads are disabled and every run gets a fresh, isolated browser profile that is discarded afterwards.</li>
        </ul>
        {config?.blocked_domains && (
          <details>
            <summary>{config.blocked_domains.length} payment, banking and identity domains are always off-limits</summary>
            <p className="domains">{config.blocked_domains.map((d) => <code key={d}>{d}</code>)}</p>
          </details>
        )}
      </section>
      <section className="card">
        <h3>Guardrails around the model</h3>
        <ul>
          <li>Text on web pages is passed to the model as untrusted data with explicit instructions never to obey it (prompt-injection defence).</li>
          <li>Hard limits: {config?.max_steps ?? 15} steps by default, {config?.max_runtime_s ?? 300}s runtime, loop detection, and automatic stop after repeated blocked actions.</li>
          <li>Every step is logged with its risk level and a screenshot, so there is a full audit trail you can export.</li>
          <li>Your LLM key, if you add one, is kept in your browser, sent only with the run request, and never stored on the server.</li>
        </ul>
      </section>
    </>
  );
}
