import {
  Concept, ConceptClass, ConceptNature, ConceptJSON,
  Edge, EdgeJSON, Source, SourceJSON,
  slugifyTopic,
} from "./schema.js";

// Frozen v0.2 threshold. Do not change without a coordinated graph rebuild.
export const DEDUP_COSINE = 0.78;

export function cosine(a: Float32Array, b: Float32Array): number {
  let dot = 0, na = 0, nb = 0;
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const x = a[i]!, y = b[i]!;
    dot += x * y;
    na += x * x;
    nb += y * y;
  }
  const ra = Math.sqrt(na), rb = Math.sqrt(nb);
  if (ra === 0 || rb === 0) return 0;
  return dot / (ra * rb);
}

export interface GraphJSON {
  sources: Record<string, SourceJSON>;
  concepts: Record<string, ConceptJSON>;
  edges: EdgeJSON[];
  stats?: unknown;
}

export class Graph {
  sources: Map<string, Source> = new Map();
  concepts: Map<string, Concept> = new Map();
  edges: Map<string, Edge> = new Map();

  // Derived indices — not serialized, rebuildable.
  byClass: Map<ConceptClass, Set<string>> = new Map();
  byNature: Map<ConceptNature, Set<string>> = new Map();
  bySession: Map<string, Set<string>> = new Map();
  childrenOf: Map<string, Set<string>> = new Map();
  openEphemerals: Set<string> = new Set();

  // ------------------------------------------------------------------
  // Inserts
  // ------------------------------------------------------------------

  addSource(s: Source): void {
    this.sources.set(s.id, s);
  }

  addConcept(c: Concept): void {
    this.concepts.set(c.id, c);
    this.indexConcept(c);
  }

  /** Insert or accumulate voices into an existing edge by identity. */
  addEdge(e: Edge): Edge {
    const existing = this.edges.get(e.id);
    if (!existing) {
      this.edges.set(e.id, e);
      return e;
    }
    for (const v of e.voices) {
      if (!existing.voices.includes(v)) existing.voices.push(v);
    }
    if (existing.voices.length >= 2 && existing.confidence === "low") {
      existing.confidence = "medium";
    }
    if (existing.voices.length >= 3 && existing.confidence !== "high") {
      existing.confidence = "high";
    }
    return existing;
  }

  // ------------------------------------------------------------------
  // Dedup + nearest-neighbor
  // ------------------------------------------------------------------

  findSimilarConcept(topic: string, embedding: Float32Array): Concept | null {
    const slug = slugifyTopic(topic);
    const canonical = this.concepts.get(slug);
    if (canonical) return canonical;

    let best: Concept | null = null;
    let bestSim = 0;
    for (const c of this.concepts.values()) {
      if (!c.embedding) continue;
      const s = cosine(embedding, c.embedding);
      if (s > bestSim) {
        bestSim = s;
        best = c;
      }
    }
    if (best && bestSim >= DEDUP_COSINE) return best;
    return null;
  }

  nearestConcepts(
    queryEmbedding: Float32Array,
    k: number = 8,
    minSim: number = 0.30,
  ): Concept[] {
    const scored: Array<[number, Concept]> = [];
    for (const c of this.concepts.values()) {
      if (!c.embedding) continue;
      const s = cosine(queryEmbedding, c.embedding);
      if (s >= minSim) scored.push([s, c]);
    }
    scored.sort((a, b) => b[0] - a[0]);
    return scored.slice(0, k).map(([, c]) => c);
  }

  openEphemeralsInSession(sessionId: string): Concept[] {
    const out: Concept[] = [];
    for (const c of this.concepts.values()) {
      if (c.class_ !== "ephemeral" || c.state !== "open") continue;
      for (const sr of c.sourceRefs) {
        const src = this.sources.get(sr);
        if (src && src.sessionId === sessionId) {
          out.push(c);
          break;
        }
      }
    }
    return out;
  }

  /**
   * R5 completion: flip open ephemerals to stale when untouched beyond
   * the age threshold. Sources without timestamps are left alone so
   * legacy graphs don't silently age out.
   */
  sweepStaleEphemerals(opts: { nowTs?: number; maxAgeDays?: number } = {}): number {
    const nowTs = opts.nowTs ?? Date.now() / 1000;
    const maxAgeSec = (opts.maxAgeDays ?? 7.0) * 86400;
    let transitioned = 0;
    for (const c of this.concepts.values()) {
      if (c.class_ !== "ephemeral" || c.state !== "open") continue;
      const src = c.lastTouchedAt ? this.sources.get(c.lastTouchedAt) : undefined;
      if (!src || src.timestamp === null) continue;
      if (nowTs - src.timestamp > maxAgeSec) {
        c.state = "stale";
        this.openEphemerals.delete(c.id);
        transitioned++;
      }
    }
    return transitioned;
  }

  // ------------------------------------------------------------------
  // Indices
  // ------------------------------------------------------------------

  private indexConcept(c: Concept): void {
    this.getOrCreate(this.byClass, c.class_).add(c.id);
    this.getOrCreate(this.byNature, c.nature).add(c.id);
    if (c.parent !== null) {
      this.getOrCreate(this.childrenOf, c.parent).add(c.id);
    }
    if (c.class_ === "ephemeral" && c.state === "open") {
      this.openEphemerals.add(c.id);
    }
    for (const sr of c.sourceRefs) {
      const sid = sr.split("#", 1)[0]!;
      this.getOrCreate(this.bySession, sid).add(c.id);
    }
  }

  private getOrCreate<K>(map: Map<K, Set<string>>, key: K): Set<string> {
    let s = map.get(key);
    if (!s) {
      s = new Set();
      map.set(key, s);
    }
    return s;
  }

  rebuildIndices(): void {
    this.byClass = new Map();
    this.byNature = new Map();
    this.bySession = new Map();
    this.childrenOf = new Map();
    this.openEphemerals = new Set();
    for (const c of this.concepts.values()) {
      this.indexConcept(c);
    }
  }

  /**
   * One-shot R1 repair: reset voices/mass for each concept and replay
   * stored source_refs through cite() with the speaker looked up from
   * sources. Used when importing graphs built outside the ingest loop.
   */
  rebuildMassFromSourceRefs(): number {
    let repaired = 0;
    for (const c of this.concepts.values()) {
      const stored = [...c.sourceRefs];
      if (stored.length === 0) continue;
      c.sourceRefs = [];
      c.voices = [];
      c.mass = 0.5;
      c.firstVoicedAt = null;
      c.lastTouchedAt = null;
      for (const sr of stored) {
        const src = this.sources.get(sr);
        const speaker = src ? src.speaker : "";
        c.cite(sr, speaker);
      }
      if (c.mass > 0.501) repaired++;
    }
    return repaired;
  }

  // ------------------------------------------------------------------
  // Serialization
  // ------------------------------------------------------------------

  toJSON(): GraphJSON {
    const sources: Record<string, SourceJSON> = {};
    for (const [id, s] of this.sources) sources[id] = s.toJSON();
    const concepts: Record<string, ConceptJSON> = {};
    for (const [id, c] of this.concepts) concepts[id] = c.toJSON();
    const edges: EdgeJSON[] = [];
    for (const e of this.edges.values()) edges.push(e.toJSON());

    const byClass: Record<string, number> = {};
    for (const [k, v] of this.byClass) byClass[k] = v.size;
    const byNature: Record<string, number> = {};
    for (const [k, v] of this.byNature) byNature[k] = v.size;
    const bySession: Record<string, number> = {};
    for (const [k, v] of this.bySession) bySession[k] = v.size;

    return {
      sources,
      concepts,
      edges,
      stats: {
        n_sources: this.sources.size,
        n_concepts: this.concepts.size,
        n_edges: this.edges.size,
        by_class: byClass,
        by_nature: byNature,
        by_session: bySession,
        open_ephemerals: this.openEphemerals.size,
      },
    };
  }

  static fromJSON(data: GraphJSON): Graph {
    const g = new Graph();
    for (const [sid, sdata] of Object.entries(data.sources ?? {})) {
      g.sources.set(sid, Source.fromJSON(sdata));
    }
    for (const [cid, cdata] of Object.entries(data.concepts ?? {})) {
      g.concepts.set(cid, Concept.fromJSON(cdata));
    }
    for (const edata of data.edges ?? []) {
      const e = Edge.fromJSON(edata);
      g.edges.set(e.id, e);
    }
    g.rebuildIndices();
    return g;
  }
}
