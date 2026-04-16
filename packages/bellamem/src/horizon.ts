import { Graph } from "./graph.js";
import { Concept, Source, logit, sigmoid, MASS_DELTA_NEW_VOICE, MASS_DELTA_REPEAT_VOICE } from "./schema.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TimelineEntry {
  turn_idx: number;
  session_id: string;
  timestamp: number | null;
  speaker: string;
}

export interface LaneEvent {
  turn_idx: number;
  mass_after: number;
  mass_delta: number;
  voice_is_new: boolean;
  speaker: string;
}

export interface Lane {
  concept_id: string;
  topic: string;
  class: Concept["class_"];
  nature: Concept["nature"];
  state: Concept["state"];
  final_mass: number;
  birth_turn: number;
  death_turn: number | null;
  events: LaneEvent[];
}

export interface Arc {
  type: string;
  from_concept: string;
  from_turn: number;
  to_concept: string;
  to_turn: number;
}

export interface HorizonData {
  timeline: TimelineEntry[];
  lanes: Lane[];
  arcs: Arc[];
  session_boundaries: number[];
  strata_order: string[];
}

// ---------------------------------------------------------------------------
// Class strata ordering — top = invariant, bottom = ephemeral
// ---------------------------------------------------------------------------

const CLASS_ORDER: Record<string, number> = {
  invariant: 0,
  decision: 1,
  observation: 2,
  ephemeral: 3,
};

const NATURE_ORDER: Record<string, number> = {
  metaphysical: 0,
  normative: 1,
  factual: 2,
};

// ---------------------------------------------------------------------------
// Builder
// ---------------------------------------------------------------------------

export interface HorizonOptions {
  /** Only include concepts with final mass >= this threshold. */
  minMass?: number;
  /** Hard cap on lanes (highest-mass concepts kept). */
  maxConcepts?: number;
  /** Restrict to specific sessions (null = all). */
  sessions?: string[] | null;
}

export function buildHorizon(graph: Graph, opts: HorizonOptions = {}): HorizonData {
  const minMass = opts.minMass ?? 0.55;
  const maxConcepts = opts.maxConcepts ?? 80;

  // 1. Build timeline — every source turn, ordered by turn_idx within
  //    each session, then by session start time across sessions.
  const allSources: Source[] = [];
  for (const s of graph.sources.values()) {
    if (opts.sessions && !opts.sessions.includes(s.sessionId)) continue;
    allSources.push(s);
  }
  // Sort by timestamp (or turn_idx as fallback)
  allSources.sort((a, b) => {
    if (a.timestamp !== null && b.timestamp !== null) return a.timestamp - b.timestamp;
    if (a.sessionId !== b.sessionId) return a.sessionId < b.sessionId ? -1 : 1;
    return a.turnIdx - b.turnIdx;
  });

  // Build a global turn index that's monotonic across sessions.
  // We remap every source to a dense 0..N index for the X axis.
  const globalIdx = new Map<string, number>(); // source_id → global X
  const timeline: TimelineEntry[] = [];
  for (let i = 0; i < allSources.length; i++) {
    const s = allSources[i]!;
    globalIdx.set(s.id, i);
    timeline.push({
      turn_idx: i,
      session_id: s.sessionId,
      timestamp: s.timestamp,
      speaker: s.speaker,
    });
  }

  // Session boundaries: the first global index of each new session_id.
  const sessionBoundaries: number[] = [];
  let lastSession = "";
  for (let i = 0; i < timeline.length; i++) {
    if (timeline[i]!.session_id !== lastSession) {
      sessionBoundaries.push(i);
      lastSession = timeline[i]!.session_id;
    }
  }

  // 2. Build lanes — one per concept, with citation events carrying
  //    mass deltas. Replay mass from source_refs (same as
  //    replayMassHistory but positioned on the global X axis).
  const rawLanes: Lane[] = [];
  for (const c of graph.concepts.values()) {
    if (c.mass < minMass) continue;

    const events: LaneEvent[] = [];
    const voices: string[] = [];
    let mass = 0.5;
    let birthTurn = Infinity;
    let deathTurn: number | null = null;

    for (const sr of c.sourceRefs) {
      const gx = globalIdx.get(sr);
      if (gx === undefined) continue; // source not in the filtered timeline
      const src = graph.sources.get(sr);
      const speaker = src ? src.speaker : "";
      const before = mass;

      if (speaker) {
        const isNew = !voices.includes(speaker);
        const delta = isNew ? MASS_DELTA_NEW_VOICE : MASS_DELTA_REPEAT_VOICE;
        if (isNew) voices.push(speaker);
        mass = sigmoid(logit(mass) + delta);
      }

      events.push({
        turn_idx: gx,
        mass_after: mass,
        mass_delta: mass - before,
        voice_is_new: speaker ? !voices.slice(0, -1).includes(speaker) || voices.length === 1 : false,
        speaker,
      });
      if (gx < birthTurn) birthTurn = gx;
    }

    if (events.length === 0) continue;

    // Death turn: if the concept was consumed/retracted, find the last
    // event — that's when the state transition happened.
    if (c.state === "consumed" || c.state === "retracted") {
      deathTurn = events[events.length - 1]!.turn_idx;
    }

    rawLanes.push({
      concept_id: c.id,
      topic: c.topic,
      class: c.class_,
      nature: c.nature,
      state: c.state,
      final_mass: c.mass,
      birth_turn: birthTurn,
      death_turn: deathTurn,
      events,
    });
  }

  // Sort by class strata → nature → mass desc → birth turn.
  rawLanes.sort((a, b) => {
    const ca = CLASS_ORDER[a.class] ?? 9;
    const cb = CLASS_ORDER[b.class] ?? 9;
    if (ca !== cb) return ca - cb;
    const na = NATURE_ORDER[a.nature] ?? 9;
    const nb = NATURE_ORDER[b.nature] ?? 9;
    if (na !== nb) return na - nb;
    if (a.final_mass !== b.final_mass) return b.final_mass - a.final_mass;
    return a.birth_turn - b.birth_turn;
  });

  // Cap at maxConcepts.
  const lanes = rawLanes.slice(0, maxConcepts);
  const laneIds = new Set(lanes.map(l => l.concept_id));

  // 3. Build arcs — edges that connect two concepts both present in
  //    the lane set. Position each arc at the turn where the edge was
  //    established (Edge.established_at → globalIdx).
  const arcs: Arc[] = [];
  for (const e of graph.edges.values()) {
    // Only structural + retract/dispute edges — skip voice-cross and
    // consume-* (noisy, don't carry structural info).
    if (e.type === "voice-cross" || e.type === "consume-success" || e.type === "consume-failure") continue;

    const srcIsLane = laneIds.has(e.source);
    const tgtIsLane = laneIds.has(e.target);
    if (!srcIsLane && !tgtIsLane) continue;

    const estGx = globalIdx.get(e.establishedAt);
    if (estGx === undefined) continue;

    // For concept→concept edges: both endpoints are lanes.
    // For turn→concept edges (retract, dispute from a turn): source is
    // a turn, target is a concept.
    let fromConcept: string;
    let fromTurn: number;
    let toConcept: string;
    let toTurn: number;

    if (srcIsLane && tgtIsLane) {
      fromConcept = e.source;
      toConcept = e.target;
      // Position from/to at their nearest citation events to estGx.
      fromTurn = nearestEvent(lanes, e.source, estGx);
      toTurn = nearestEvent(lanes, e.target, estGx);
    } else if (tgtIsLane) {
      // turn → concept edge (retract, dispute voiced by a turn)
      fromConcept = e.target; // collapse to same lane, arc to self
      fromTurn = estGx;
      toConcept = e.target;
      toTurn = nearestEvent(lanes, e.target, estGx);
    } else {
      continue; // source is a lane but target isn't — skip
    }

    arcs.push({
      type: e.type,
      from_concept: fromConcept,
      from_turn: fromTurn,
      to_concept: toConcept,
      to_turn: toTurn,
    });
  }

  // Deduplicate arcs by (type, from_concept, to_concept).
  const seenArcs = new Set<string>();
  const dedupedArcs = arcs.filter(a => {
    const key = `${a.type}|${a.from_concept}|${a.to_concept}`;
    if (seenArcs.has(key)) return false;
    seenArcs.add(key);
    return true;
  });

  const strataOrder = ["invariant", "decision", "observation", "ephemeral"];
  return { timeline, lanes, arcs: dedupedArcs, session_boundaries: sessionBoundaries, strata_order: strataOrder };
}

function nearestEvent(lanes: Lane[], conceptId: string, targetGx: number): number {
  for (const l of lanes) {
    if (l.concept_id !== conceptId) continue;
    let best = l.events[0]?.turn_idx ?? targetGx;
    let bestDist = Math.abs(best - targetGx);
    for (const ev of l.events) {
      const d = Math.abs(ev.turn_idx - targetGx);
      if (d < bestDist) { bestDist = d; best = ev.turn_idx; }
    }
    return best;
  }
  return targetGx;
}
