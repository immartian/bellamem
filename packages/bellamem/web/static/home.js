// bellamem home page — list all discovered projects
function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
}

async function load() {
  const projects = await fetch("/api/projects").then(r => r.json());
  const totals = projects.reduce((acc, p) => {
    if (p.error) return acc;
    acc.concepts += p.n_concepts ?? 0;
    acc.edges    += p.n_edges ?? 0;
    acc.sources  += p.n_sources ?? 0;
    acc.disputes += p.n_disputes ?? 0;
    acc.open     += p.n_open_ephemerals ?? 0;
    return acc;
  }, { concepts: 0, edges: 0, sources: 0, disputes: 0, open: 0 });

  document.getElementById("topstats").textContent =
    `${projects.length} projects · ${totals.concepts} concepts · ${totals.edges} edges · ${totals.disputes} disputes · ${totals.open} open`;

  if (projects.length === 0) {
    document.getElementById("app").innerHTML = `
      <div class="card">
        <h2>no projects found</h2>
        <div class="muted">bellamem didn't find any <code>.graph/v02.json</code> under your Claude Code projects.</div>
        <div class="muted" style="margin-top:8px">run <code>bellamem save</code> in a project to create one, or add an explicit root to <code>~/.config/bellamem/projects.json</code>:</div>
        <pre style="background:var(--panel-2);padding:10px;border-radius:4px;margin-top:10px">{ "roots": ["/abs/path/to/your/project"] }</pre>
      </div>`;
    return;
  }

  const cards = projects.map(p => {
    if (p.error) {
      return `<div class="card"><h3>${esc(p.label)}</h3><div class="muted">error: ${esc(p.error)}</div></div>`;
    }
    const when = p.lastActivityMs ? new Date(p.lastActivityMs).toLocaleString() : "—";
    const flagPills = (p.red_flags ?? []).map(f =>
      `<span class="pill ${f.verdict}">${f.name}</span>`
    ).join(" ");
    const encoded = encodeURIComponent(p.id);
    return `
      <div class="card">
        <div style="display:flex;align-items:center;gap:10px">
          <h3 style="margin:0;flex:1">${esc(p.label)}</h3>
          <span class="muted" style="font-size:11px">${esc(p.origin)}</span>
        </div>
        <div class="muted" style="font-size:11px;word-break:break-all">${esc(p.absPath)}</div>
        <div style="margin:10px 0;display:grid;grid-template-columns:repeat(4,1fr);gap:8px">
          <div><div class="muted" style="font-size:10px;text-transform:uppercase">concepts</div><div style="font-size:18px;font-weight:600">${p.n_concepts}</div></div>
          <div><div class="muted" style="font-size:10px;text-transform:uppercase">edges</div><div style="font-size:18px;font-weight:600">${p.n_edges}</div></div>
          <div><div class="muted" style="font-size:10px;text-transform:uppercase">disputes</div><div style="font-size:18px;font-weight:600">${p.n_disputes}</div></div>
          <div><div class="muted" style="font-size:10px;text-transform:uppercase">open</div><div style="font-size:18px;font-weight:600">${p.n_open_ephemerals}</div></div>
        </div>
        <div style="margin-bottom:10px">${flagPills || '<span class="pill ok">all green</span>'}</div>
        <div class="muted" style="font-size:11px;margin-bottom:8px">last activity: ${esc(when)}</div>
        <div style="display:flex;gap:8px">
          <a href="/p/${encoded}/overview">overview</a>
          <a href="/p/${encoded}/graph">graph</a>
          <a href="/p/${encoded}/trace">trace</a>
        </div>
      </div>`;
  }).join("");

  document.getElementById("app").innerHTML = `<div class="grid cols-2">${cards}</div>`;
}

function connectWatch() {
  const es = new EventSource("/api/watch");
  es.addEventListener("reload", () => load().catch(() => {}));
  es.onerror = () => { es.close(); setTimeout(connectWatch, 1500); };
}

load().catch(err => {
  document.getElementById("app").innerHTML = `<div class="card">error: ${esc(err.message)}</div>`;
});
connectWatch();
