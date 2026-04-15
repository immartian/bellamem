import { Graph } from "./graph.js";
import { Concept, Edge, Source, logit, sigmoid, MASS_DELTA_NEW_VOICE, MASS_DELTA_REPEAT_VOICE } from "./schema.js";

// ---------------------------------------------------------------------------
// Session summaries
// ---------------------------------------------------------------------------

export interface SessionSummary {
  session_id: string;
  n_turns: number;
  first_ts: number | null;
  last_ts: number | null;
  n_concepts_touched: number;
}

export function listSessions(graph: Graph): SessionSummary[] {
  const summaries = new Map<string, SessionSummary>();
  for (const s of graph.sources.values()) {
    let sum = summaries.get(s.sessionId);
    if (!sum) {
      sum = {
        session_id: s.sessionId,
        n_turns: 0,
        first_ts: null,
        last_ts: null,
        n_concepts_touched: 0,
      };
      summaries.set(s.sessionId, sum);
    }
    sum.n_turns++;
    if (s.timestamp !== null) {
      if (sum.first_ts === null || s.timestamp < sum.first_ts) sum.first_ts = s.timestamp;
      if (sum.last_ts === null || s.timestamp > sum.last_ts) sum.last_ts = s.timestamp;
    }
  }
  // n_concepts_touched via by_session index (rebuilt on load)
  for (const [sid, set] of graph.bySession) {
    const sum = summaries.get(sid);
    if (sum) sum.n_concepts_touched = set.size;
  }
  return [...summaries.values()].sort((a, b) => {
    const ta = a.last_ts ?? -Infinity;
    const tb = b.last_ts ?? -Infinity;
    return tb - ta;
  });
}

// ---------------------------------------------------------------------------
// Mass replay — reproduce a concept's mass trajectory from source_refs
// ---------------------------------------------------------------------------

export interface MassStep {
  source_id: string;
  speaker: string;
  mass_before: number;
  mass_after: number;
  voice_is_new: boolean;
}

/**
 * Replay the R1 cite() sequence for a concept and return the mass
 * trajectory. The final mass_after must equal concept.mass — the trace
 * view uses this as a cross-check against drift in the rebuild path.
 */
export function replayMassHistory(graph: Graph, concept: Concept): MassStep[] {
  const steps: MassStep[] = [];
  const voices: string[] = [];
  let mass = 0.5;
  for (const sr of concept.sourceRefs) {
    const src = graph.sources.get(sr);
    const speaker = src ? src.speaker : "";
    const before = mass;
    if (!speaker) {
      steps.push({
        source_id: sr, speaker: "", mass_before: before, mass_after: mass,
        voice_is_new: false,
      });
      continue;
    }
    const isNew = !voices.includes(speaker);
    const delta = isNew ? MASS_DELTA_NEW_VOICE : MASS_DELTA_REPEAT_VOICE;
    if (isNew) voices.push(speaker);
    mass = sigmoid(logit(mass) + delta);
    steps.push({
      source_id: sr, speaker, mass_before: before, mass_after: mass,
      voice_is_new: isNew,
    });
  }
  return steps;
}

// ---------------------------------------------------------------------------
// Session trace unroll
// ---------------------------------------------------------------------------

export interface ConceptTouch {
  concept_id: string;
  topic: string;
  class: Concept["class_"];
  nature: Concept["nature"];
  state: Concept["state"];
  mass_before: number;
  mass_after: number;
  voice_is_new: boolean;
  kind: "create" | "cite";
}

export interface EdgeTouch {
  type: Edge["type"];
  source: string;
  target: string;
  source_topic: string | null;
  target_topic: string | null;
}

export interface TraceTurn {
  turn_idx: number;
  source_id: string;
  speaker: string;
  text_preview: string;
  timestamp: number | null;
  concepts: ConceptTouch[];
  edges: EdgeTouch[];
}

export interface SessionTrace {
  session_id: string;
  n_turns: number;
  turns: TraceTurn[];
}

/**
 * Unroll a session into per-turn traces. For each turn:
 *   - concepts: created or cited at this turn (with the mass delta)
 *   - edges: established_at this turn
 *
 * The mass deltas are computed by the same replay path used by
 * replayMassHistory. Client-side code SHOULD NOT independently
 * reimplement R1 — it can consume these deltas as-is.
 */
export function sessionTrace(graph: Graph, sessionId: string): SessionTrace | null {
  const sources: Source[] = [];
  for (const s of graph.sources.values()) {
    if (s.sessionId === sessionId) sources.push(s);
  }
  if (sources.length === 0) return null;
  sources.sort((a, b) => a.turnIdx - b.turnIdx);

  // Pre-compute per-source mass steps for every concept touched in
  // this session. Each step maps { source_id -> { concept_id -> MassStep } }.
  const stepsBySrc = new Map<string, Map<string, MassStep>>();
  for (const c of graph.concepts.values()) {
    const sessionHits = c.sourceRefs.filter(sr => sr.startsWith(`${sessionId}#`));
    if (sessionHits.length === 0) continue;
    const full = replayMassHistory(graph, c);
    for (let i = 0; i < c.sourceRefs.length; i++) {
      const sr = c.sourceRefs[i]!;
      const step = full[i]!;
      let inner = stepsBySrc.get(sr);
      if (!inner) { inner = new Map(); stepsBySrc.set(sr, inner); }
      inner.set(c.id, step);
    }
  }

  // Index edges by established_at
  const edgesBySrc = new Map<string, Edge[]>();
  for (const e of graph.edges.values()) {
    let arr = edgesBySrc.get(e.establishedAt);
    if (!arr) { arr = []; edgesBySrc.set(e.establishedAt, arr); }
    arr.push(e);
  }

  const turns: TraceTurn[] = [];
  for (const s of sources) {
    const touches: ConceptTouch[] = [];
    const innerSteps = stepsBySrc.get(s.id);
    if (innerSteps) {
      for (const [cid, step] of innerSteps) {
        const c = graph.concepts.get(cid);
        if (!c) continue;
        const kind = c.firstVoicedAt === s.id ? "create" : "cite";
        touches.push({
          concept_id: cid,
          topic: c.topic,
          class: c.class_,
          nature: c.nature,
          state: c.state,
          mass_before: step.mass_before,
          mass_after: step.mass_after,
          voice_is_new: step.voice_is_new,
          kind,
        });
      }
    }
    touches.sort((a, b) => (b.mass_after - b.mass_before) - (a.mass_after - a.mass_before));

    const edgeTouches: EdgeTouch[] = [];
    const edgeList = edgesBySrc.get(s.id) ?? [];
    for (const e of edgeList) {
      edgeTouches.push({
        type: e.type,
        source: e.source,
        target: e.target,
        source_topic: graph.concepts.get(e.source)?.topic ?? null,
        target_topic: graph.concepts.get(e.target)?.topic ?? null,
      });
    }

    turns.push({
      turn_idx: s.turnIdx,
      source_id: s.id,
      speaker: s.speaker,
      text_preview: s.text,
      timestamp: s.timestamp,
      concepts: touches,
      edges: edgeTouches,
    });
  }

  return {
    session_id: sessionId,
    n_turns: turns.length,
    turns,
  };
}

// ---------------------------------------------------------------------------
// Mass histogram — 10 buckets, shared with the overview client
// ---------------------------------------------------------------------------

export function massHistogram(graph: Graph): number[] {
  const buckets = new Array<number>(10).fill(0);
  for (const c of graph.concepts.values()) {
    let idx = Math.floor((c.mass - 0.5) * 20);
    if (idx < 0) idx = 0;
    if (idx > 9) idx = 9;
    buckets[idx]!++;
  }
  return buckets;
}
