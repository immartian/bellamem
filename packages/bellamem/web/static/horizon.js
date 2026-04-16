// bellamem horizon — time × structure event horizon view
// Labels inside bars, auto-fit width, no horizontal scroll.
import { rewriteNavLinks } from "./nav.js";
const PREFIX = location.pathname.match(/^\/p\/[^/]+/)?.[0] ?? "";
rewriteNavLinks();

const CLASS_COLORS = {
  invariant:   "#7cc4ff",
  decision:    "#ffd06b",
  observation: "#9cf196",
  ephemeral:   "#c79cff",
};
const ARC_COLORS = {
  cause:     "#e6e8ee",
  elaborate: "#7cc4ff",
  dispute:   "#ff7a7a",
  retract:   "#ff7a7a",
  support:   "#6ef0c2",
};
const LANE_H = 30;
const LANE_GAP = 4;
const STRATA_GAP = 14;
const STRATA_LABEL_H = 18;
const PAD_LEFT = 12;
const PAD_TOP = 10;
const PAD_RIGHT = 12;

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
}

let currentData = null;

async function load() {
  const minMass = parseFloat(document.getElementById("min-mass").value);
  const maxConcepts = parseInt(document.getElementById("max-concepts").value, 10);
  const data = await fetch(
    `${PREFIX}/api/horizon?min_mass=${minMass}&max_concepts=${maxConcepts}`
  ).then(r => r.json());
  currentData = data;
  document.getElementById("topstats").textContent =
    `${data.lanes.length} concepts · ${data.arcs.length} arcs · ${data.timeline.length} turns`;
  document.getElementById("meta").textContent =
    `${data.session_boundaries.length} sessions`;
  render(data);
}

function render(data) {
  const svg = document.getElementById("svg");
  svg.innerHTML = "";

  if (data.lanes.length === 0) {
    svg.setAttribute("width", "400");
    svg.setAttribute("height", "80");
    addText(svg, 20, 40, "no concepts above the mass threshold", "#8a90a6", 12);
    return;
  }

  // --- Compute active turns (turns that have at least one event) ---
  // Map each active turn to a dense X index so gaps compress.
  const activeTurns = new Set();
  for (const lane of data.lanes) {
    for (const ev of lane.events) activeTurns.add(ev.turn_idx);
  }
  const sorted = [...activeTurns].sort((a, b) => a - b);
  // Insert gap markers: if two consecutive active turns are far apart,
  // add a fractional spacing proportional to the gap (capped).
  const denseX = new Map();
  let dx = 0;
  for (let i = 0; i < sorted.length; i++) {
    denseX.set(sorted[i], dx);
    if (i + 1 < sorted.length) {
      const gap = sorted[i + 1] - sorted[i];
      dx += gap > 10 ? 2 : 1; // compress large gaps to 2 units
    }
  }
  const maxDx = dx || 1;

  // Auto-fit: scale X so the content fills the viewport width.
  const wrap = document.querySelector(".horizon-wrap");
  const viewW = (wrap ? wrap.clientWidth : 1200) - PAD_LEFT - PAD_RIGHT - 20;
  const TURN_PX = Math.max(3, viewW / maxDx);

  const xOf = (turnIdx) => {
    const d = denseX.get(turnIdx);
    return PAD_LEFT + (d !== undefined ? d : 0) * TURN_PX;
  };

  // --- Y layout: strata → lanes ---
  const strataGroups = new Map();
  for (const l of data.lanes) {
    if (!strataGroups.has(l.class)) strataGroups.set(l.class, []);
    strataGroups.get(l.class).push(l);
  }

  let y = PAD_TOP;
  const laneY = new Map();
  const strataY = [];

  for (const stratum of data.strata_order) {
    const group = strataGroups.get(stratum);
    if (!group || group.length === 0) continue;
    strataY.push({ label: stratum, y });
    y += STRATA_LABEL_H;
    for (const lane of group) {
      laneY.set(lane.concept_id, y);
      y += LANE_H + LANE_GAP;
    }
    y += STRATA_GAP;
  }

  const totalW = PAD_LEFT + maxDx * TURN_PX + PAD_RIGHT;
  const totalH = y + 20;
  svg.setAttribute("width", String(Math.max(totalW, viewW + PAD_LEFT + PAD_RIGHT)));
  svg.setAttribute("height", String(totalH));

  // --- Session boundary bands ---
  // Map session boundaries to dense X space.
  const bandGroup = mkG(svg, "session-bands");
  for (let i = 0; i < data.session_boundaries.length; i++) {
    const rawStart = data.session_boundaries[i];
    const rawEnd = i + 1 < data.session_boundaries.length
      ? data.session_boundaries[i + 1]
      : data.timeline.length;
    // Find the nearest dense-X position for start/end.
    const sx = nearestDenseX(sorted, denseX, rawStart) * TURN_PX + PAD_LEFT;
    const ex = nearestDenseX(sorted, denseX, rawEnd) * TURN_PX + PAD_LEFT;
    if (i % 2 === 0) {
      addRect(bandGroup, sx, PAD_TOP, ex - sx, totalH - PAD_TOP - 20,
        "rgba(30,35,55,0.35)", 0);
    }
  }

  // --- Strata headers ---
  for (const s of strataY) {
    addText(svg, PAD_LEFT, s.y + 12, s.label.toUpperCase(), "#8a90a6", 10, "bold");
    // Thin separator line.
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", PAD_LEFT); line.setAttribute("x2", totalW - PAD_RIGHT);
    line.setAttribute("y1", s.y + STRATA_LABEL_H - 2);
    line.setAttribute("y2", s.y + STRATA_LABEL_H - 2);
    line.setAttribute("stroke", "#262b3d"); line.setAttribute("stroke-width", "1");
    svg.appendChild(line);
  }

  // --- Concept bars (lanes) ---
  const lanesGroup = mkG(svg, "lanes");
  for (const lane of data.lanes) {
    const ly = laneY.get(lane.concept_id);
    if (ly === undefined) continue;
    const color = CLASS_COLORS[lane.class] ?? "#888";
    const isDead = lane.state === "retracted" || lane.state === "consumed";

    // Bar from birth to death/end.
    const x1 = xOf(lane.birth_turn);
    const x2 = lane.death_turn !== null
      ? xOf(lane.death_turn) + TURN_PX
      : totalW - PAD_RIGHT;
    const barW = Math.max(60, x2 - x1); // min width so label fits

    // Bar background.
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", x1);
    rect.setAttribute("y", ly);
    rect.setAttribute("width", barW);
    rect.setAttribute("height", LANE_H);
    rect.setAttribute("rx", "4");
    rect.setAttribute("fill", color);
    rect.setAttribute("fill-opacity", isDead ? "0.08" : String(0.1 + lane.final_mass * 0.12));
    rect.setAttribute("stroke", color);
    rect.setAttribute("stroke-opacity", isDead ? "0.15" : "0.3");
    rect.setAttribute("stroke-width", "1");
    if (isDead) rect.setAttribute("stroke-dasharray", "3 3");
    lanesGroup.appendChild(rect);

    // Label INSIDE the bar.
    const maxLabelW = barW - 50; // leave room for mass badge
    const labelText = truncate(lane.topic, Math.floor(maxLabelW / 6.5));
    const label = addText(lanesGroup, x1 + 6, ly + LANE_H / 2 + 4,
      labelText, color, 11);
    label.style.cursor = "pointer";
    label.onclick = () => openConcept(lane.concept_id);

    // Mass badge at right end of bar.
    addText(lanesGroup, x1 + barW - 38, ly + LANE_H / 2 + 4,
      lane.final_mass.toFixed(2), "#8a90a6", 10);

    // State badge for dead lanes.
    if (isDead) {
      const badge = lane.state === "retracted" ? "✕" : "●";
      addText(lanesGroup, x1 + barW - 12, ly + LANE_H / 2 + 4,
        badge, "#ff7a7a", 11);
    }

    // Citation event dots along the TOP edge of the bar.
    for (const ev of lane.events) {
      const cx = xOf(ev.turn_idx);
      const cy_dot = ly + 4;
      const r = 3 + ev.mass_delta * 25;
      const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("cx", cx);
      dot.setAttribute("cy", cy_dot);
      dot.setAttribute("r", String(Math.max(2, Math.min(6, r))));
      dot.setAttribute("fill", color);
      dot.setAttribute("fill-opacity", String(0.6 + ev.mass_after * 0.4));
      dot.setAttribute("stroke", ev.voice_is_new ? "#fff" : "#0f1115");
      dot.setAttribute("stroke-width", ev.voice_is_new ? "1.5" : "0.5");
      dot.style.cursor = "pointer";
      dot.onmouseenter = (e) => showTooltip(e, lane, ev, data.timeline[ev.turn_idx]);
      dot.onmouseleave = hideTooltip;
      lanesGroup.appendChild(dot);
    }
  }

  // --- Arcs ---
  const arcsGroup = mkG(svg, "arcs");
  for (const arc of data.arcs) {
    const y1 = laneY.get(arc.from_concept);
    const y2 = laneY.get(arc.to_concept);
    if (y1 === undefined || y2 === undefined) continue;
    const x1 = xOf(arc.from_turn);
    const x2 = xOf(arc.to_turn);
    if (x1 === x2 && y1 === y2) continue;

    // Offset Y to top/bottom of the bar depending on direction.
    const fy = arc.type === "cause" || arc.type === "elaborate" ? y1 : y1 + LANE_H;
    const ty = arc.type === "cause" || arc.type === "elaborate" ? y2 : y2 + LANE_H;

    const mx = (x1 + x2) / 2;
    const my = (fy + ty) / 2;
    const spread = Math.min(50, Math.abs(x2 - x1) * 0.25 + 12);
    const cpY = arc.type === "cause" || arc.type === "elaborate"
      ? Math.min(fy, ty) - spread
      : Math.max(fy, ty) + spread;

    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", `M ${x1} ${fy} Q ${mx} ${cpY} ${x2} ${ty}`);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", ARC_COLORS[arc.type] ?? "#666");
    path.setAttribute("stroke-width", "1.3");
    path.setAttribute("opacity", "0.65");
    if (arc.type === "dispute") path.setAttribute("stroke-dasharray", "5 3");
    if (arc.type === "retract") path.setAttribute("stroke-dasharray", "2 3");
    arcsGroup.appendChild(path);

    // Arrowhead.
    const angle = Math.atan2(ty - cpY, x2 - mx);
    const aL = 5;
    const arrow = document.createElementNS("http://www.w3.org/2000/svg", "path");
    arrow.setAttribute("d",
      `M ${x2 - aL * Math.cos(angle - 0.4)} ${ty - aL * Math.sin(angle - 0.4)} ` +
      `L ${x2} ${ty} ` +
      `L ${x2 - aL * Math.cos(angle + 0.4)} ${ty - aL * Math.sin(angle + 0.4)}`);
    arrow.setAttribute("stroke", ARC_COLORS[arc.type] ?? "#666");
    arrow.setAttribute("stroke-width", "1.1");
    arrow.setAttribute("fill", "none");
    arrow.setAttribute("opacity", "0.65");
    arcsGroup.appendChild(arrow);

    // Dispute midpoint marker.
    if (arc.type === "dispute") {
      addText(arcsGroup, mx - 4, (fy + cpY) / 2 + 4, "⊥", "#ff7a7a", 11, "bold");
    }
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function nearestDenseX(sorted, denseX, rawIdx) {
  // Binary search for the closest active turn.
  let lo = 0, hi = sorted.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] === rawIdx) return denseX.get(sorted[mid]) ?? 0;
    if (sorted[mid] < rawIdx) lo = mid + 1; else hi = mid - 1;
  }
  // Return the nearest.
  const candidates = [];
  if (lo < sorted.length) candidates.push(sorted[lo]);
  if (hi >= 0) candidates.push(sorted[hi]);
  let best = 0, bestDist = Infinity;
  for (const c of candidates) {
    const d = Math.abs(c - rawIdx);
    if (d < bestDist) { bestDist = d; best = denseX.get(c) ?? 0; }
  }
  return best;
}

function truncate(s, n) {
  if (n < 4) return s.slice(0, 1) + "…";
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

function mkG(parent, cls) {
  const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
  if (cls) g.setAttribute("class", cls);
  parent.appendChild(g);
  return g;
}

function addRect(parent, x, y, w, h, fill, opacity) {
  const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  r.setAttribute("x", x); r.setAttribute("y", y);
  r.setAttribute("width", Math.max(0, w)); r.setAttribute("height", Math.max(0, h));
  r.setAttribute("fill", fill ?? "none");
  if (opacity !== undefined) r.setAttribute("opacity", String(opacity));
  parent.appendChild(r);
  return r;
}

function addText(parent, x, y, text, fill, size, weight) {
  const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
  t.setAttribute("x", x); t.setAttribute("y", y);
  t.setAttribute("fill", fill ?? "#e6e8ee");
  t.setAttribute("font-size", String(size ?? 12));
  t.setAttribute("font-family", "ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, Consolas, monospace");
  if (weight) t.setAttribute("font-weight", weight);
  t.textContent = text;
  parent.appendChild(t);
  return t;
}

function showTooltip(event, lane, ev, timelineEntry) {
  const tip = document.getElementById("tooltip");
  const when = timelineEntry?.timestamp
    ? new Date(timelineEntry.timestamp * 1000).toLocaleString()
    : `turn ${ev.turn_idx}`;
  tip.innerHTML = `
    <div><b>${esc(lane.topic)}</b></div>
    <div style="color:#8a90a6">${lane.class}/${lane.nature}${lane.state ? " · " + lane.state : ""}</div>
    <div>mass: ${ev.mass_after.toFixed(3)}${ev.mass_delta > 0 ? ` <span style="color:#9cf196">(+${ev.mass_delta.toFixed(3)})</span>` : ""}</div>
    <div>${ev.voice_is_new ? "★ new voice · " : ""}${ev.speaker} · ${when}</div>
  `;
  tip.style.display = "block";
  tip.style.left = (event.clientX + 12) + "px";
  tip.style.top = (event.clientY - 10) + "px";
}

function hideTooltip() {
  document.getElementById("tooltip").style.display = "none";
}

async function openConcept(cid) {
  const url = `${PREFIX}/api/concept/${encodeURIComponent(cid)}`;
  const d = await fetch(url).then(r => r.json());
  if (d.error) return;
  const c = d.concept;
  const hist = d.mass_history.map(h =>
    `${h.source_id} [${h.speaker}] ${h.mass_before.toFixed(2)}→${h.mass_after.toFixed(2)}${h.voice_is_new ? " ★" : ""}`
  ).join("\n");
  const w = window.open("", "_blank", "width=500,height=600");
  if (!w) return;
  w.document.title = c.topic;
  w.document.body.style.cssText = "background:#0f1115;color:#e6e8ee;font:12px/1.6 monospace;padding:16px";
  w.document.body.innerHTML = `
    <h3 style="color:${CLASS_COLORS[c.class] ?? '#fff'}">${esc(c.topic)}</h3>
    <div>${c.class} / ${c.nature}${c.state ? " · " + c.state : ""}</div>
    <div>mass: <b>${c.mass.toFixed(3)}</b> · voices: ${c.voices.join(", ")} · refs: ${c.source_refs.length}</div>
    <h4>mass history</h4><pre>${esc(hist)}</pre>
    <h4>incoming (${d.incoming_edges.length})</h4>
    <pre>${d.incoming_edges.map(e => `${e.type} ← ${esc(e.source_topic ?? e.source)}`).join("\n") || "(none)"}</pre>
    <h4>outgoing (${d.outgoing_edges.length})</h4>
    <pre>${d.outgoing_edges.map(e => `${e.type} → ${esc(e.target_topic ?? e.target)}`).join("\n") || "(none)"}</pre>
  `;
}

// --- Controls ---
document.getElementById("min-mass").oninput = () => load();
document.getElementById("max-concepts").onchange = () => load();

// --- Live reload ---
function connectWatch() {
  const es = new EventSource(`${PREFIX}/api/watch`);
  es.addEventListener("reload", () => load());
  es.onerror = () => { es.close(); setTimeout(connectWatch, 1500); };
}

load().catch(err => {
  document.getElementById("svg").innerHTML = "";
  document.getElementById("meta").textContent = `error: ${err.message}`;
});
connectWatch();
