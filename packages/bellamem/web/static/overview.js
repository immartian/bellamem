// bellamem overview page — project-scoped
const PREFIX = location.pathname.match(/^\/p\/[^/]+/)?.[0] ?? "";

async function load() {
  const [audit, graph, sessions] = await Promise.all([
    fetch(`${PREFIX}/api/audit`).then(r => r.json()),
    fetch(`${PREFIX}/api/graph`).then(r => r.json()),
    fetch(`${PREFIX}/api/sessions`).then(r => r.json()),
  ]);
  if (audit.project) {
    const crumb = document.createElement("a");
    crumb.href = "/";
    crumb.textContent = `← all projects · ${audit.project.label}`;
    crumb.style.marginRight = "12px";
    const nav = document.querySelector("header.topbar nav");
    if (nav && !nav.querySelector(".crumb")) { crumb.className = "crumb"; nav.prepend(crumb); }
  }
  render(audit, graph, sessions);
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
}

function render(audit, graph, sessions) {
  const concepts = Object.values(graph.concepts);
  const stats = graph.stats ?? {};
  document.getElementById("topstats").textContent =
    `${stats.n_concepts} concepts · ${stats.n_edges} edges · ${stats.n_sources} sources · ${sessions.length} sessions`;

  const verdictPill = (v) => `<span class="pill ${v.verdict}">${v.name} ${v.verdict}</span>`;
  const signalsHtml = audit.signals.map(s =>
    `<div>${verdictPill(s)} <span class="muted">${esc(s.note)}</span></div>`
  ).join("");

  const maxH = Math.max(1, ...audit.histogram);
  const histHtml = audit.histogram.map((n, i) => {
    const pct = (n / maxH) * 100;
    const lo = (0.5 + i * 0.05).toFixed(2);
    const hi = (0.55 + i * 0.05).toFixed(2);
    return `<div class="bar" style="height:${pct}%" title="[${lo}, ${hi}) — ${n} concepts"><span class="n">${n || ""}</span></div>`;
  }).join("");

  // Class × nature grid
  const classes = ["invariant", "decision", "observation", "ephemeral"];
  const natures = ["factual", "normative", "metaphysical"];
  const byKey = new Map();
  for (const c of concepts) {
    const k = `${c.class}|${c.nature}`;
    if (!byKey.has(k)) byKey.set(k, []);
    byKey.get(k).push(c);
  }
  const cells = [];
  for (const cls of classes) {
    for (const nat of natures) {
      const key = `${cls}|${nat}`;
      const arr = (byKey.get(key) ?? []).slice().sort((a, b) => b.mass - a.mass);
      const top3 = arr.slice(0, 3).map(c =>
        `<li class="class-${cls}">${c.mass.toFixed(2)} · ${esc(c.topic)}</li>`
      ).join("");
      cells.push(
        `<div class="cell">
           <div class="title class-${cls}">${cls} × ${nat}</div>
           <div class="n">${arr.length}</div>
           <ul>${top3}</ul>
         </div>`
      );
    }
  }

  // Recent decisions
  const decisions = concepts
    .filter(c => c.class === "decision")
    .sort((a, b) => (b.last_touched_at ?? "").localeCompare(a.last_touched_at ?? ""))
    .slice(0, 10);
  const decisionsHtml = decisions.map(c =>
    `<div class="muted">[decision/${c.nature}] <span style="color:var(--fg)">${esc(c.topic)}</span></div>`
  ).join("") || `<div class="muted">(none)</div>`;

  // Session panorama
  const sessionsHtml = sessions.slice(0, 8).map(s => {
    const when = s.last_ts ? new Date(s.last_ts * 1000).toLocaleString() : "untimed";
    return `<div class="cell">
      <div class="title">${esc(s.session_id)}</div>
      <div class="n">${s.n_turns}t</div>
      <div class="muted">${s.n_concepts_touched} concepts · ${when}</div>
      <div><a href="${PREFIX}/trace?session=${encodeURIComponent(s.session_id)}">open trace →</a></div>
    </div>`;
  }).join("");

  document.getElementById("app").innerHTML = `
    <div class="grid cols-2">
      <div class="card">
        <h2>audit signals</h2>
        ${signalsHtml}
        <div style="margin-top:10px" class="muted">
          ephemerals: open=${audit.ephemeral.open} consumed=${audit.ephemeral.consumed}
          retracted=${audit.ephemeral.retracted} stale=${audit.ephemeral.stale}
        </div>
      </div>
      <div class="card">
        <h2>mass distribution</h2>
        <div class="hist">${histHtml}</div>
        <div class="hist-axis"><span>0.5</span><span>0.75</span><span>1.0</span></div>
      </div>
    </div>

    <h2>class × nature</h2>
    <div class="grid cols-3">${cells.join("")}</div>

    <h2>recent decisions</h2>
    <div class="card">${decisionsHtml}</div>

    <h2>sessions</h2>
    <div class="grid cols-4">${sessionsHtml}</div>
  `;
}

// Live reload via SSE — project-scoped
function connectWatch() {
  const es = new EventSource(`${PREFIX}/api/watch`);
  es.addEventListener("reload", () => window.location.reload());
  es.onerror = () => { es.close(); setTimeout(connectWatch, 1500); };
}

load().catch(err => {
  document.getElementById("app").innerHTML = `<div class="card">error: ${esc(err.message)}</div>`;
});
connectWatch();
