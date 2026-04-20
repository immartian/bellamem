import { Graph, cosine } from "./graph.js";
import { Concept, ConceptClass, EdgeType } from "./schema.js";
import type { Embedder } from "./clients.js";

function tokenize(q: string): string[] {
  const out: string[] = [];
  for (const m of q.toLowerCase().matchAll(/[a-z0-9]+/g)) {
    if (m[0].length >= 2) out.push(m[0]);
  }
  return out;
}

function substringScore(text: string, qTokens: string[]): number {
  if (qTokens.length === 0) return 0;
  const low = text.toLowerCase();
  let hits = 0;
  for (const t of qTokens) if (low.includes(t)) hits++;
  return hits / qTokens.length;
}

function scoreConcept(
  c: Concept,
  qTokens: string[],
  qEmb: Float32Array | null,
): number {
  const sub = substringScore(c.topic, qTokens);
  let cos = 0;
  if (qEmb && c.embedding) cos = cosine(qEmb, c.embedding);
  const base = Math.max(sub, cos);
  if (base <= 0) return 0;
  return base * c.mass;
}

const EDGE_SECTIONS: Array<[EdgeType, string]> = [
  ["dispute",   "⊥ disputes"],
  ["retract",   "retracts"],
  ["cause",     "causes"],
  ["support",   "supports"],
  ["elaborate", "elaborates"],
];

const ARROWS: Record<string, string> = {
  dispute:   "  ⊥  ",
  retract:   "  retract  ",
  cause:     "  ⇒  ",
  support:   "  +  ",
  elaborate: "  →  ",
};

type Neighbors = Map<EdgeType, Array<[string, string, "in" | "out"]>>;

function collectNeighbors(graph: Graph, seedIds: Set<string>): Neighbors {
  const out: Neighbors = new Map();
  const push = (t: EdgeType, tup: [string, string, "in" | "out"]) => {
    let arr = out.get(t);
    if (!arr) { arr = []; out.set(t, arr); }
    arr.push(tup);
  };
  for (const e of graph.edges.values()) {
    if (seedIds.has(e.source)) {
      const srcC = graph.concepts.get(e.source);
      if (!srcC) continue;
      const tgtC = graph.concepts.get(e.target);
      const srcLabel = srcC.topic;
      const tgtLabel = tgtC ? tgtC.topic : `(turn ${e.target})`;
      push(e.type, [srcLabel, tgtLabel, "out"]);
    } else if (seedIds.has(e.target)) {
      const tgtC = graph.concepts.get(e.target);
      if (!tgtC) continue;
      const srcC = graph.concepts.get(e.source);
      const tgtLabel = tgtC.topic;
      const srcLabel = srcC ? srcC.topic : `(turn ${e.source})`;
      push(e.type, [srcLabel, tgtLabel, "in"]);
    }
  }
  return out;
}

export interface AskTextOptions {
  embedder?: Embedder;
  seedK?: number;
  classFilter?: ConceptClass;
  minScore?: number;
}

export async function askText(
  graph: Graph,
  focus: string,
  opts: AskTextOptions = {},
): Promise<string> {
  const seedK = opts.seedK ?? 12;
  const minScore = opts.minScore ?? 0.05;

  if (graph.concepts.size === 0) {
    return "# v0.2 ask\n  empty graph — run `bellamem save` first";
  }

  const qTokens = tokenize(focus);
  let qEmb: Float32Array | null = null;

  if (opts.embedder && focus.trim()) {
    try {
      qEmb = await opts.embedder.embed(focus);
    } catch {
      qEmb = null;
    }
    if (qEmb) {
      // Hydrate concept embeddings FROM CACHE ONLY — never issue new
      // API calls for the ~N topics in the graph. A cache miss means
      // the concept falls back to substring scoring, which is fine.
      // Without this guard, a graph with 1000 concepts on a cold cache
      // would fire 1000 sequential OpenAI requests and hang for minutes.
      for (const c of graph.concepts.values()) {
        if (c.embedding) continue;
        const cached = opts.embedder.embedCached(c.topic);
        if (cached) c.embedding = cached;
      }
    }
  }

  const scored: Array<[number, Concept]> = [];
  for (const c of graph.concepts.values()) {
    if (opts.classFilter && c.class_ !== opts.classFilter) continue;
    const s = scoreConcept(c, qTokens, qEmb);
    if (s >= minScore) scored.push([s, c]);
  }
  scored.sort((a, b) => b[0] - a[0]);
  const seeds = scored.slice(0, seedK);
  const seedIds = new Set(seeds.map(([, c]) => c.id));

  const mode = qEmb ? "cosine+mass" : "substring+mass";
  const out: string[] = [];
  out.push(`# v0.2 ask (focus: '${focus}')`);
  out.push(
    `  ${graph.concepts.size} concepts · ${graph.edges.size} edges · ` +
    `${scored.length} candidates · mode=${mode}`,
  );
  if (opts.classFilter) out.push(`  class_filter: ${opts.classFilter}`);
  out.push("");

  if (seeds.length === 0) {
    out.push("## no matches");
    out.push(`  no concepts scored ≥ ${minScore} for tokens ${qTokens.length ? JSON.stringify(qTokens) : "(none)"}`);
    out.push("  try broader wording, or run `bellamem resume` for the full pack");
    return out.join("\n");
  }

  out.push(`## relevant concepts (${seeds.length})`);
  for (const [score, c] of seeds) {
    const state = c.state ? ` [${c.state}]` : "";
    out.push(
      `  s=${score.toFixed(2)} m=${c.mass.toFixed(2)} ` +
      `[${c.class_}/${c.nature}]${state} ${c.topic}`,
    );
  }
  out.push("");

  const neighbors = collectNeighbors(graph, seedIds);
  let totalNeighbors = 0;
  for (const [etype, label] of EDGE_SECTIONS) {
    const pairs = neighbors.get(etype);
    if (!pairs || pairs.length === 0) continue;
    const seen = new Set<string>();
    const unique: Array<[string, string, string]> = [];
    for (const [src, tgt, dir] of pairs) {
      const key = `${src}\x00${tgt}`;
      if (seen.has(key)) continue;
      seen.add(key);
      unique.push([src, tgt, dir]);
    }
    out.push(`## ${label} (${unique.length})`);
    const arrow = ARROWS[etype] ?? "  →  ";
    for (const [src, tgt] of unique) {
      out.push(`  ${src}${arrow}${tgt}`);
    }
    out.push("");
    totalNeighbors += unique.length;
  }

  out.push(`— ${seeds.length} seeds, ${totalNeighbors} edge neighbors —`);
  return out.join("\n");
}
