// bellamem force-directed graph view — project-scoped
// Depends on global d3 (loaded from CDN in graph.html)
import { rewriteNavLinks } from "./nav.js";
const PREFIX = location.pathname.match(/^\/p\/[^/]+/)?.[0] ?? "";
rewriteNavLinks();

const CLASS_COLORS = {
  invariant: "#7cc4ff",
  decision: "#ffd06b",
  observation: "#9cf196",
  ephemeral: "#c79cff",
};
const EDGE_STYLE = {
  dispute:          { color: "#ff7a7a", width: 2,   dash: "6 4" },
  retract:          { color: "#ff7a7a", width: 1.5, dash: "2 3" },
  cause:            { color: "#e6e8ee", width: 1.6, dash: null  },
  elaborate:        { color: "#7cc4ff", width: 1.2, dash: null  },
  support:          { color: "#6ef0c2", width: 0.8, dash: null  },
  "voice-cross":    { color: "#444a5c", width: 0.5, dash: null  },
  "consume-success":{ color: "#6ef0c2", width: 1.2, dash: "3 3" },
  "consume-failure":{ color: "#ff7a7a", width: 1.2, dash: "3 3" },
};

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
}

async function load() {
  const graph = await fetch(`${PREFIX}/api/graph`).then(r => r.json());
  const stats = graph.stats ?? {};
  document.getElementById("topstats").textContent =
    `${stats.n_concepts} concepts · ${stats.n_edges} edges · ${stats.n_sources} sources`;
  render(graph);
}

let sim = null;
let currentGraph = null;

function render(graph) {
  currentGraph = graph;
  const classFilter = document.getElementById("class-filter").value;
  const massMin = parseFloat(document.getElementById("mass-min").value);
  const colorBySession = document.getElementById("session-color").checked;

  const conceptArr = Object.values(graph.concepts);
  const kept = conceptArr.filter(c => {
    if (classFilter && c.class !== classFilter) return false;
    if (c.mass < massMin) return false;
    return true;
  });
  const keptIds = new Set(kept.map(c => c.id));

  // Build session color scale
  const sessionIds = [...new Set(Object.values(graph.sources).map(s => s.session_id))].sort();
  const sessionScale = d3.scaleOrdinal()
    .domain(sessionIds)
    .range(d3.schemeTableau10);

  const nodes = kept.map(c => ({
    id: c.id,
    topic: c.topic,
    class: c.class,
    nature: c.nature,
    state: c.state,
    mass: c.mass,
    refs: (c.source_refs || []).length,
    first_session: c.first_voiced_at ? String(c.first_voiced_at).split("#")[0] : null,
  }));

  const links = [];
  for (const e of graph.edges) {
    if (!keptIds.has(e.source) || !keptIds.has(e.target)) continue;
    if (e.type === "voice-cross") continue;  // noisy — hide in default view
    links.push({ source: e.source, target: e.target, type: e.type });
  }

  const svg = d3.select("#svg");
  svg.selectAll("*").remove();
  const width = svg.node().clientWidth;
  const height = svg.node().clientHeight;

  const g = svg.append("g");
  svg.call(d3.zoom().scaleExtent([0.2, 4]).on("zoom", (ev) => g.attr("transform", ev.transform)));

  const link = g.append("g").attr("class", "links")
    .selectAll("line")
    .data(links).enter().append("line")
    .attr("stroke", d => EDGE_STYLE[d.type]?.color ?? "#666")
    .attr("stroke-width", d => EDGE_STYLE[d.type]?.width ?? 1)
    .attr("stroke-dasharray", d => EDGE_STYLE[d.type]?.dash ?? null)
    .attr("stroke-opacity", 0.7);

  const node = g.append("g").attr("class", "nodes")
    .selectAll("circle")
    .data(nodes).enter().append("circle")
    .attr("r", d => Math.max(3, Math.min(14, 3 + Math.log2(1 + d.refs) * 2.5)))
    .attr("fill", d => colorBySession && d.first_session
      ? sessionScale(d.first_session)
      : CLASS_COLORS[d.class] ?? "#888")
    .attr("fill-opacity", d => 0.4 + (d.mass - 0.5) * 1.2)
    .attr("stroke", "#0f1115").attr("stroke-width", 0.6)
    .style("cursor", "pointer")
    .on("click", (ev, d) => openDrawer(d.id))
    .call(drag());

  node.append("title").text(d => `${d.topic}  m=${d.mass.toFixed(2)}  [${d.class}/${d.nature}]`);

  if (sim) sim.stop();
  sim = d3.forceSimulation(nodes)
    .force("charge", d3.forceManyBody().strength(-90))
    .force("link",   d3.forceLink(links).id(d => d.id).distance(60).strength(0.5))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collide", d3.forceCollide(14))
    .alpha(1).alphaDecay(0.04)
    .on("tick", () => {
      link
        .attr("x1", d => d.source.x).attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
      node.attr("cx", d => d.x).attr("cy", d => d.y);
    });
}

function drag() {
  return d3.drag()
    .on("start", (ev, d) => {
      if (!ev.active) sim.alphaTarget(0.3).restart();
      d.fx = d.x; d.fy = d.y;
    })
    .on("drag", (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
    .on("end", (ev, d) => {
      if (!ev.active) sim.alphaTarget(0);
      d.fx = null; d.fy = null;
    });
}

async function openDrawer(cid) {
  const d = await fetch(`${PREFIX}/api/concept/${encodeURIComponent(cid)}`).then(r => r.json());
  const { concept } = d;
  const sourcesHtml = d.source_refs_expanded.map(s =>
    `<div class="muted">${s.source_id} [${s.speaker ?? "?"}]<br>${esc((s.text_preview ?? "").slice(0, 180))}</div>`
  ).join("<hr style='border-color:var(--border)'>");
  const incomingHtml = d.incoming_edges.map(e =>
    `<div class="muted">${e.type} ← ${esc(e.source_topic ?? e.source)}</div>`
  ).join("") || "<div class='muted'>(none)</div>";
  const outgoingHtml = d.outgoing_edges.map(e =>
    `<div class="muted">${e.type} → ${esc(e.target_topic ?? e.target)}</div>`
  ).join("") || "<div class='muted'>(none)</div>";
  const massHtml = d.mass_history.map(s =>
    `<div class="muted">${s.source_id} [${s.speaker}] ${s.mass_before.toFixed(2)}→${s.mass_after.toFixed(2)}${s.voice_is_new ? " ★" : ""}</div>`
  ).join("");

  document.getElementById("drawer-body").innerHTML = `
    <h3 class="class-${concept.class}">${esc(concept.topic)}</h3>
    <div class="muted">${concept.class} / ${concept.nature}${concept.state ? ` · ${concept.state}` : ""}</div>
    <div>mass: <b>${concept.mass.toFixed(3)}</b> · voices: ${concept.voices.join(", ")} · refs: ${concept.source_refs.length}</div>
    <h3>mass history</h3>${massHtml}
    <h3>incoming edges</h3>${incomingHtml}
    <h3>outgoing edges</h3>${outgoingHtml}
    <h3>sources</h3>${sourcesHtml}
  `;
  document.getElementById("drawer").classList.add("open");
}

document.getElementById("drawer-close").onclick = () =>
  document.getElementById("drawer").classList.remove("open");
document.getElementById("class-filter").onchange = () => render(currentGraph);
document.getElementById("mass-min").oninput = () => render(currentGraph);
document.getElementById("session-color").onchange = () => render(currentGraph);
document.getElementById("reheat").onclick = () => { if (sim) sim.alpha(1).restart(); };

function connectWatch() {
  const es = new EventSource(`${PREFIX}/api/watch`);
  es.addEventListener("reload", () => window.location.reload());
  es.onerror = () => { es.close(); setTimeout(connectWatch, 1500); };
}
load();
connectWatch();
