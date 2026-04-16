// bellamem horizon — time × structure event horizon view
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
const LANE_H = 22;
const LANE_PAD = 3;
const EVENT_R = 4;
const LEFT_MARGIN = 220;
const TOP_MARGIN = 40;
const TURN_W = 8;
const STRATA_GAP = 18;
const STRATA_LABEL_H = 16;

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
    svg.setAttribute("height", "100");
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", "20"); t.setAttribute("y", "50");
    t.setAttribute("fill", "#8a90a6"); t.textContent = "no concepts above the mass threshold";
    svg.appendChild(t);
    return;
  }

  // Compute layout dimensions.
  const maxTurn = data.timeline.length;
  const strataGroups = groupByStrata(data.lanes, data.strata_order);

  // Y positions: strata header → lanes within each stratum.
  let y = TOP_MARGIN;
  const laneY = new Map();  // concept_id → y center
  const strataY = [];       // [{label, y, height}]

  for (const stratum of data.strata_order) {
    const group = strataGroups.get(stratum);
    if (!group || group.length === 0) continue;
    strataY.push({ label: stratum, y, height: group.length * (LANE_H + LANE_PAD) + STRATA_LABEL_H + STRATA_GAP });
    y += STRATA_LABEL_H;
    for (const lane of group) {
      laneY.set(lane.concept_id, y + LANE_H / 2);
      y += LANE_H + LANE_PAD;
    }
    y += STRATA_GAP;
  }

  const totalW = LEFT_MARGIN + maxTurn * TURN_W + 40;
  const totalH = y + 20;
  svg.setAttribute("width", String(totalW));
  svg.setAttribute("height", String(totalH));
  svg.setAttribute("viewBox", `0 0 ${totalW} ${totalH}`);

  const xOf = (turnIdx) => LEFT_MARGIN + turnIdx * TURN_W;

  // --- Session boundary bands ---
  const bandGroup = mkG(svg, "session-bands");
  for (let i = 0; i < data.session_boundaries.length; i++) {
    const start = data.session_boundaries[i];
    const end = i + 1 < data.session_boundaries.length
      ? data.session_boundaries[i + 1]
      : maxTurn;
    if (i % 2 === 0) {
      const rect = mkRect(bandGroup, xOf(start), TOP_MARGIN - 10,
        (end - start) * TURN_W, totalH - TOP_MARGIN, "session-band");
      rect.setAttribute("data-session", data.timeline[start]?.session_id ?? "");
    }
  }

  // --- Strata headers ---
  for (const s of strataY) {
    const t = mkText(svg, 8, s.y + 11, s.label, "stratum-label");
  }

  // --- Lanes ---
  const lanesGroup = mkG(svg, "lanes");
  for (const lane of data.lanes) {
    const cy = laneY.get(lane.concept_id);
    if (cy === undefined) continue;
    const color = CLASS_COLORS[lane.class] ?? "#888";

    // Lane background bar from birth to death (or end).
    const x1 = xOf(lane.birth_turn);
    const x2 = lane.death_turn !== null
      ? xOf(lane.death_turn) + TURN_W
      : totalW - 20;
    const isDead = lane.state === "retracted" || lane.state === "consumed";
    const bg = mkRect(lanesGroup, x1, cy - LANE_H / 2 + 1, x2 - x1, LANE_H - 2,
      `lane-bg${isDead ? " " + lane.state : ""}`);
    bg.setAttribute("fill-opacity", String(0.08 + lane.final_mass * 0.15));

    // Lane label on the left.
    const label = mkText(lanesGroup, 8, cy + 4,
      truncate(lane.topic, 28), "lane-label");
    label.setAttribute("fill", color);
    label.setAttribute("data-cid", lane.concept_id);
    label.onclick = () => openConcept(lane.concept_id);

    // Mass badge.
    mkText(lanesGroup, LEFT_MARGIN - 35, cy + 4,
      lane.final_mass.toFixed(2), "lane-label").setAttribute("fill", "#8a90a6");

    // Citation event dots.
    for (const ev of lane.events) {
      const cx = xOf(ev.turn_idx);
      const r = EVENT_R + ev.mass_delta * 30; // bigger delta = bigger dot
      const dot = mkCircle(lanesGroup, cx, cy, Math.max(2.5, Math.min(7, r)),
        "event-dot");
      dot.setAttribute("fill", color);
      dot.setAttribute("fill-opacity", String(0.5 + ev.mass_after * 0.5));
      if (ev.voice_is_new) {
        dot.setAttribute("stroke", "#fff");
        dot.setAttribute("stroke-width", "1.5");
      }
      dot.onmouseenter = (e) => showTooltip(e, lane, ev, data.timeline[ev.turn_idx]);
      dot.onmouseleave = hideTooltip;
    }

    // Death marker.
    if (isDead && lane.death_turn !== null) {
      const dx = xOf(lane.death_turn);
      const marker = mkText(lanesGroup, dx + 2, cy + 4,
        lane.state === "retracted" ? "✕" : "●", "");
      marker.setAttribute("fill", "#ff7a7a");
      marker.setAttribute("font-size", "11");
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
    if (x1 === x2 && y1 === y2) continue; // self-arc, skip

    // Bezier control point: arc curves away from the midpoint.
    // For cause edges (which reach back in time), curve upward.
    // For dispute edges, curve outward from the lane midpoint.
    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2;
    const spread = Math.min(60, Math.abs(x2 - x1) * 0.3 + 15);
    const cy_ctrl = arc.type === "cause" || arc.type === "elaborate"
      ? my - spread    // curve upward
      : my + spread;   // curve downward for dispute/retract

    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", `M ${x1} ${y1} Q ${mx} ${cy_ctrl} ${x2} ${y2}`);
    path.setAttribute("class", `arc ${arc.type}`);
    path.setAttribute("stroke", ARC_COLORS[arc.type] ?? "#666");

    // Arrow marker at the target end.
    const angle = Math.atan2(y2 - cy_ctrl, x2 - mx);
    const aLen = 6;
    const ax1 = x2 - aLen * Math.cos(angle - 0.4);
    const ay1 = y2 - aLen * Math.sin(angle - 0.4);
    const ax2 = x2 - aLen * Math.cos(angle + 0.4);
    const ay2 = y2 - aLen * Math.sin(angle + 0.4);

    const arrow = document.createElementNS("http://www.w3.org/2000/svg", "path");
    arrow.setAttribute("d", `M ${ax1} ${ay1} L ${x2} ${y2} L ${ax2} ${ay2}`);
    arrow.setAttribute("stroke", ARC_COLORS[arc.type] ?? "#666");
    arrow.setAttribute("stroke-width", "1.2");
    arrow.setAttribute("fill", "none");
    arrow.setAttribute("class", `arc ${arc.type}`);

    arcsGroup.appendChild(path);
    arcsGroup.appendChild(arrow);

    // Dispute X marker at midpoint.
    if (arc.type === "dispute") {
      const xm = mkText(arcsGroup, mx - 5, my + 4, "⊥", "");
      xm.setAttribute("fill", "#ff7a7a");
      xm.setAttribute("font-size", "12");
      xm.setAttribute("font-weight", "bold");
    }
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function groupByStrata(lanes, order) {
  const map = new Map();
  for (const l of lanes) {
    if (!map.has(l.class)) map.set(l.class, []);
    map.get(l.class).push(l);
  }
  return map;
}

function truncate(s, n) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

function mkG(parent, cls) {
  const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
  if (cls) g.setAttribute("class", cls);
  parent.appendChild(g);
  return g;
}

function mkRect(parent, x, y, w, h, cls) {
  const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  r.setAttribute("x", x); r.setAttribute("y", y);
  r.setAttribute("width", Math.max(0, w)); r.setAttribute("height", Math.max(0, h));
  if (cls) r.setAttribute("class", cls);
  parent.appendChild(r);
  return r;
}

function mkCircle(parent, cx, cy, r, cls) {
  const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  c.setAttribute("cx", cx); c.setAttribute("cy", cy); c.setAttribute("r", r);
  if (cls) c.setAttribute("class", cls);
  parent.appendChild(c);
  return c;
}

function mkText(parent, x, y, text, cls) {
  const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
  t.setAttribute("x", x); t.setAttribute("y", y);
  if (cls) t.setAttribute("class", cls);
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
    <div class="muted">${lane.class}/${lane.nature}${lane.state ? " · " + lane.state : ""}</div>
    <div>mass: ${ev.mass_after.toFixed(3)}${ev.mass_delta > 0 ? ` (+${ev.mass_delta.toFixed(3)})` : ""}</div>
    <div>${ev.voice_is_new ? "★ new voice" : ""} [${ev.speaker}]</div>
    <div class="muted">${when} · session ${timelineEntry?.session_id ?? "?"}</div>
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
  // Simple alert-style popup — reuse drawer from graph view if we had
  // it, but for now a focused view.
  const w = window.open("", "_blank", "width=500,height=600");
  if (!w) return;
  const c = d.concept;
  const hist = d.mass_history.map(h =>
    `${h.source_id} [${h.speaker}] ${h.mass_before.toFixed(2)}→${h.mass_after.toFixed(2)}${h.voice_is_new ? " ★" : ""}`
  ).join("\n");
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
