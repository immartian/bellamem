import { createHash } from "node:crypto";

export type ConceptClass = "invariant" | "decision" | "observation" | "ephemeral";
export type ConceptNature = "factual" | "normative" | "metaphysical";
export type ConceptState = "open" | "consumed" | "retracted" | "stale";
export type EdgeType =
  | "support" | "dispute" | "cause" | "elaborate"
  | "voice-cross" | "retract"
  | "consume-success" | "consume-failure";
export type Confidence = "low" | "medium" | "high";

export const VALID_CLASSES: ReadonlySet<ConceptClass> = new Set([
  "invariant", "decision", "observation", "ephemeral",
]);
export const VALID_NATURES: ReadonlySet<ConceptNature> = new Set([
  "factual", "normative", "metaphysical",
]);
export const VALID_STATES: ReadonlySet<ConceptState> = new Set([
  "open", "consumed", "retracted", "stale",
]);
export const VALID_EDGE_TYPES: ReadonlySet<EdgeType> = new Set([
  "support", "dispute", "cause", "elaborate",
  "voice-cross", "retract",
  "consume-success", "consume-failure",
]);

export const MASS_DELTA_NEW_VOICE = 0.5;
export const MASS_DELTA_REPEAT_VOICE = 0.1;

export function logit(p: number): number {
  const q = Math.min(Math.max(p, 1e-6), 1 - 1e-6);
  return Math.log(q / (1 - q));
}

export function sigmoid(x: number): number {
  if (x > 30) return 1.0 - 1e-6;
  if (x < -30) return 1e-6;
  return 1.0 / (1.0 + Math.exp(-x));
}

export function slugifyTopic(topic: string): string {
  const s = topic.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
  return s || "unnamed";
}

// ---------------------------------------------------------------------------
// Source — append-only pointer to a speaker turn
// ---------------------------------------------------------------------------

export interface SourceJSON {
  session_id: string;
  file_path: string;
  speaker: string;
  turn_idx: number;
  text_preview: string;
  timestamp: number | null;
}

export class Source {
  readonly sessionId: string;
  readonly filePath: string;
  readonly speaker: string;
  readonly turnIdx: number;
  readonly text: string;
  readonly timestamp: number | null;

  constructor(opts: {
    sessionId: string;
    filePath: string;
    speaker: string;
    turnIdx: number;
    text: string;
    timestamp?: number | null;
  }) {
    this.sessionId = opts.sessionId;
    this.filePath = opts.filePath;
    this.speaker = opts.speaker;
    this.turnIdx = opts.turnIdx;
    this.text = opts.text;
    this.timestamp = opts.timestamp ?? null;
  }

  get id(): string {
    return `${this.sessionId}#${this.turnIdx}`;
  }

  toJSON(): SourceJSON {
    return {
      session_id: this.sessionId,
      file_path: this.filePath,
      speaker: this.speaker,
      turn_idx: this.turnIdx,
      text_preview: this.text.slice(0, 200),
      timestamp: this.timestamp,
    };
  }

  static fromJSON(data: SourceJSON): Source {
    return new Source({
      sessionId: data.session_id,
      filePath: data.file_path ?? "",
      speaker: data.speaker,
      turnIdx: data.turn_idx,
      text: data.text_preview ?? "",
      timestamp: data.timestamp ?? null,
    });
  }
}

// ---------------------------------------------------------------------------
// Concept — topic-keyed project node
// ---------------------------------------------------------------------------

export interface ConceptJSON {
  id: string;
  topic: string;
  class: ConceptClass;
  nature: ConceptNature;
  parent: string | null;
  state: ConceptState | null;
  mass: number;
  mass_floor: number;
  voices: string[];
  source_refs: string[];
  first_voiced_at: string | null;
  last_touched_at: string | null;
}

export class Concept {
  id: string;
  topic: string;
  class_: ConceptClass;
  nature: ConceptNature;
  parent: string | null;
  state: ConceptState | null;
  mass: number;
  massFloor: number;
  voices: string[];
  sourceRefs: string[];
  firstVoicedAt: string | null;
  lastTouchedAt: string | null;
  embedding: Float32Array | null;

  constructor(opts: {
    id: string;
    topic: string;
    class_: ConceptClass;
    nature: ConceptNature;
    parent?: string | null;
    state?: ConceptState | null;
    mass?: number;
    massFloor?: number;
    voices?: string[];
    sourceRefs?: string[];
    firstVoicedAt?: string | null;
    lastTouchedAt?: string | null;
    embedding?: Float32Array | null;
  }) {
    if (!VALID_CLASSES.has(opts.class_)) {
      throw new Error(`invalid class: ${opts.class_}`);
    }
    if (!VALID_NATURES.has(opts.nature)) {
      throw new Error(`invalid nature: ${opts.nature}`);
    }
    let state = opts.state ?? null;
    if (state !== null && !VALID_STATES.has(state)) {
      throw new Error(`invalid state: ${state}`);
    }
    if (opts.class_ !== "ephemeral" && state !== null) {
      throw new Error(`state is only valid for ephemeral class (got class=${opts.class_})`);
    }
    if (opts.class_ === "ephemeral" && state === null) {
      state = "open";
    }

    this.id = opts.id;
    this.topic = opts.topic;
    this.class_ = opts.class_;
    this.nature = opts.nature;
    this.parent = opts.parent ?? null;
    this.state = state;
    this.mass = opts.mass ?? 0.5;
    this.massFloor = opts.massFloor ?? 0.0;
    this.voices = opts.voices ? [...opts.voices] : [];
    this.sourceRefs = opts.sourceRefs ? [...opts.sourceRefs] : [];
    this.firstVoicedAt = opts.firstVoicedAt ?? null;
    this.lastTouchedAt = opts.lastTouchedAt ?? null;
    this.embedding = opts.embedding ?? null;
  }

  /** R1 accumulate: append source_ref (if new) and bump mass on a new speaker. */
  cite(sourceId: string, speaker: string = ""): void {
    if (this.sourceRefs.includes(sourceId)) return;
    this.sourceRefs.push(sourceId);
    if (this.firstVoicedAt === null) this.firstVoicedAt = sourceId;
    this.lastTouchedAt = sourceId;

    if (!speaker) return;

    const newVoice = !this.voices.includes(speaker);
    let delta: number;
    if (newVoice) {
      this.voices.push(speaker);
      delta = MASS_DELTA_NEW_VOICE;
    } else {
      delta = MASS_DELTA_REPEAT_VOICE;
    }
    this.mass = sigmoid(logit(this.mass) + delta);
  }

  toJSON(): ConceptJSON {
    return {
      id: this.id,
      topic: this.topic,
      class: this.class_,
      nature: this.nature,
      parent: this.parent,
      state: this.state,
      mass: this.mass,
      mass_floor: this.massFloor,
      voices: [...this.voices],
      source_refs: [...this.sourceRefs],
      first_voiced_at: this.firstVoicedAt,
      last_touched_at: this.lastTouchedAt,
    };
  }

  static fromJSON(data: ConceptJSON): Concept {
    return new Concept({
      id: data.id,
      topic: data.topic,
      class_: data.class,
      nature: data.nature,
      parent: data.parent ?? null,
      state: data.state ?? null,
      mass: data.mass ?? 0.5,
      massFloor: data.mass_floor ?? 0.0,
      voices: data.voices ?? [],
      sourceRefs: data.source_refs ?? [],
      firstVoicedAt: data.first_voiced_at ?? null,
      lastTouchedAt: data.last_touched_at ?? null,
    });
  }
}

// ---------------------------------------------------------------------------
// Edge — first-class typed relationship
// ---------------------------------------------------------------------------

export interface EdgeJSON {
  id: string;
  type: EdgeType;
  source: string;
  target: string;
  established_at: string;
  voices: string[];
  confidence: Confidence;
}

export class Edge {
  type: EdgeType;
  source: string;
  target: string;
  establishedAt: string;
  voices: string[];
  confidence: Confidence;

  constructor(opts: {
    type: EdgeType;
    source: string;
    target: string;
    establishedAt: string;
    voices?: string[];
    confidence?: Confidence;
  }) {
    if (!VALID_EDGE_TYPES.has(opts.type)) {
      throw new Error(`invalid edge type: ${opts.type}`);
    }
    this.type = opts.type;
    this.source = opts.source;
    this.target = opts.target;
    this.establishedAt = opts.establishedAt;
    this.voices = opts.voices ? [...opts.voices] : [];
    this.confidence = opts.confidence ?? "medium";
  }

  get id(): string {
    return createHash("sha256")
      .update(`${this.type}|${this.source}|${this.target}`)
      .digest("hex")
      .slice(0, 16);
  }

  toJSON(): EdgeJSON {
    return {
      id: this.id,
      type: this.type,
      source: this.source,
      target: this.target,
      established_at: this.establishedAt,
      voices: [...this.voices],
      confidence: this.confidence,
    };
  }

  static fromJSON(data: EdgeJSON): Edge {
    return new Edge({
      type: data.type,
      source: data.source,
      target: data.target,
      establishedAt: data.established_at,
      voices: data.voices ?? [],
      confidence: data.confidence ?? "medium",
    });
  }
}
