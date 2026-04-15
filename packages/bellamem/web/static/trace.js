// bellamem session trace — turn-by-turn replay, project-scoped
import { rewriteNavLinks } from "./nav.js";
const PREFIX = location.pathname.match(/^\/p\/[^/]+/)?.[0] ?? "";
rewriteNavLinks();

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
}

let currentTrace = null;
let currentIdx = 0;

async function loadSessions() {
  const sessions = await fetch(`${PREFIX}/api/sessions`).then(r => r.json());
  const stats = await fetch(`${PREFIX}/api/audit`).then(r => r.json());
  document.getElementById("topstats").textContent =
    `${stats.nConcepts} concepts · ${stats.nEdges} edges · ${sessions.length} sessions`;
  const picker = document.getElementById("session-picker");
  picker.innerHTML = sessions.map(s => {
    const when = s.last_ts ? new Date(s.last_ts * 1000).toLocaleString() : "untimed";
    return `<option value="${esc(s.session_id)}">${esc(s.session_id)} — ${s.n_turns} turns · ${when}</option>`;
  }).join("");
  // Prefer a session passed in the URL
  const url = new URL(window.location.href);
  const qs = url.searchParams.get("session");
  if (qs && sessions.find(s => s.session_id === qs)) {
    picker.value = qs;
  }
  picker.onchange = () => loadTrace(picker.value);
  if (picker.value) loadTrace(picker.value);
}

async function loadTrace(sid) {
  const trace = await fetch(`${PREFIX}/api/session/${encodeURIComponent(sid)}/trace`).then(r => r.json());
  if (trace.error) {
    document.getElementById("turn-panel").textContent = trace.error;
    return;
  }
  currentTrace = trace;
  const scrub = document.getElementById("scrub");
  scrub.max = String(Math.max(0, trace.n_turns - 1));
  scrub.value = String(Math.max(0, trace.n_turns - 1));
  currentIdx = trace.n_turns - 1;
  document.getElementById("session-meta").textContent =
    `${trace.n_turns} turns in ${sid}`;
  render();
}

function render() {
  if (!currentTrace) return;
  const t = currentTrace.turns[currentIdx];
  if (!t) return;

  // Accumulate mass state at this point in the trace
  // Every concept's "current" mass = the latest mass_after up to currentIdx
  const massState = new Map();
  for (let i = 0; i <= currentIdx; i++) {
    const turn = currentTrace.turns[i];
    for (const c of turn.concepts) {
      massState.set(c.concept_id, { topic: c.topic, class: c.class, mass: c.mass_after, last_touch_idx: i });
    }
  }

  // Turn panel
  const tp = document.getElementById("turn-panel");
  const createdHtml = t.concepts.filter(c => c.kind === "create").map(c =>
    `<div>+ <span class="class-${c.class}">${esc(c.topic)}</span>  <span class="muted">[${c.class}/${c.nature}]  m=${c.mass_after.toFixed(2)}</span></div>`
  ).join("") || "<div class='muted'>(none)</div>";
  const citedHtml = t.concepts.filter(c => c.kind === "cite").map(c => {
    const delta = (c.mass_after - c.mass_before).toFixed(2);
    const arrow = c.voice_is_new ? "★" : " ";
    return `<div>${arrow} <span class="class-${c.class}">${esc(c.topic)}</span>  <span class="muted">${c.mass_before.toFixed(2)}→${c.mass_after.toFixed(2)} (+${delta})</span></div>`;
  }).join("") || "<div class='muted'>(none)</div>";
  const edgesHtml = t.edges.map(e => {
    const src = e.source_topic ?? e.source;
    const tgt = e.target_topic ?? e.target;
    return `<div class="edge-row">${esc(src)} —${e.type}→ ${esc(tgt)}</div>`;
  }).join("") || "<div class='muted'>(none)</div>";

  const when = t.timestamp ? new Date(t.timestamp * 1000).toLocaleString() : "untimed";
  tp.innerHTML = `
    <div class="meta">#${t.turn_idx} [${t.speaker}] · ${when}</div>
    <pre>${esc(t.text_preview)}</pre>
    <h3>created (${t.concepts.filter(c=>c.kind==="create").length})</h3>${createdHtml}
    <h3>cited (${t.concepts.filter(c=>c.kind==="cite").length})</h3>${citedHtml}
    <h3>edges (${t.edges.length})</h3>${edgesHtml}
  `;

  // Live state panel — mass distribution of touched concepts so far
  const touched = [...massState.values()].sort((a, b) => b.mass - a.mass);
  const topHtml = touched.slice(0, 25).map(m => {
    const recent = m.last_touch_idx === currentIdx ? " ←" : "";
    return `<div class="touch"><span class="topic class-${m.class}">${esc(m.topic)}</span> <span class="delta">m=${m.mass.toFixed(2)}${recent}</span></div>`;
  }).join("");
  document.getElementById("state-panel").innerHTML = `
    <h3>live state at turn ${currentIdx} / ${currentTrace.n_turns - 1}</h3>
    <div class="muted">${touched.length} concepts touched so far</div>
    ${topHtml}
  `;

  document.getElementById("count").textContent = `${currentIdx + 1} / ${currentTrace.n_turns}`;
  document.getElementById("scrub").value = String(currentIdx);
}

document.getElementById("scrub").oninput = (e) => {
  currentIdx = parseInt(e.target.value, 10);
  render();
};
document.getElementById("prev").onclick = () => {
  if (currentIdx > 0) { currentIdx--; render(); }
};
document.getElementById("next").onclick = () => {
  if (currentTrace && currentIdx < currentTrace.n_turns - 1) { currentIdx++; render(); }
};
window.addEventListener("keydown", (e) => {
  if (e.key === "ArrowLeft") { if (currentIdx > 0) { currentIdx--; render(); } }
  if (e.key === "ArrowRight") { if (currentTrace && currentIdx < currentTrace.n_turns - 1) { currentIdx++; render(); } }
});

function connectWatch() {
  const es = new EventSource(`${PREFIX}/api/watch`);
  es.addEventListener("reload", () => window.location.reload());
  es.onerror = () => { es.close(); setTimeout(connectWatch, 1500); };
}
loadSessions();
connectWatch();
