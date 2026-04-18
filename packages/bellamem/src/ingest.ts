import { createReadStream } from "node:fs";
import { createInterface } from "node:readline";
import { basename } from "node:path";
import {
  Concept, ConceptClass, ConceptNature, Edge, EdgeType, Source,
  VALID_CLASSES, VALID_NATURES, slugifyTopic,
} from "./schema.js";
import { Graph } from "./graph.js";
import { saveGraph } from "./store.js";
import { Embedder, TurnClassifier, ClassifyResult } from "./clients.js";

export const CONTEXT_K = 8;
export const RECENT_TURN_N = 3;
export const MAX_TURN_CHARS = 1500;

// ---------------------------------------------------------------------------
// jsonl reading
// ---------------------------------------------------------------------------

interface JsonlRecord {
  type?: string;
  sessionId?: string;
  timestamp?: string;
  message?: { content?: unknown };
}

function extractTurnText(msg: JsonlRecord): string {
  const content = msg.message?.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    const parts: string[] = [];
    for (const item of content) {
      if (item && typeof item === "object" && (item as Record<string, unknown>).type === "text") {
        const t = (item as Record<string, unknown>).text;
        if (typeof t === "string") parts.push(t);
      }
    }
    return parts.join("\n");
  }
  return "";
}

function parseTimestamp(raw: unknown): number | null {
  if (typeof raw !== "string" || raw.length === 0) return null;
  try {
    const norm = raw.endsWith("Z") ? raw.slice(0, -1) + "+00:00" : raw;
    const ms = Date.parse(norm);
    if (Number.isNaN(ms)) return null;
    return ms / 1000;
  } catch {
    return null;
  }
}

async function scanSessionId(jsonlPath: string, maxLines: number = 20): Promise<string> {
  const rl = createInterface({
    input: createReadStream(jsonlPath, { encoding: "utf8" }),
    crlfDelay: Infinity,
  });
  let i = 0;
  try {
    for await (const line of rl) {
      if (i++ >= maxLines) break;
      try {
        const rec = JSON.parse(line) as JsonlRecord;
        if (typeof rec.sessionId === "string" && rec.sessionId.length > 0) {
          return rec.sessionId.slice(0, 8);
        }
      } catch { /* skip */ }
    }
  } finally {
    rl.close();
  }
  return basename(jsonlPath).replace(/\.[^.]+$/, "").slice(0, 8);
}

/**
 * Stream a Claude Code jsonl session and return Sources in order.
 * MUST stream (large sessions OOM otherwise). Tail uses a bounded buffer.
 */
export async function readSessionTurns(
  jsonlPath: string,
  opts: { tail?: number | null } = {},
): Promise<Source[]> {
  const tail = opts.tail ?? null;
  const sessionId = await scanSessionId(jsonlPath);
  const buf: Source[] = [];

  const rl = createInterface({
    input: createReadStream(jsonlPath, { encoding: "utf8" }),
    crlfDelay: Infinity,
  });

  let idx = 0;
  try {
    for await (const raw of rl) {
      const line = raw.replace(/\n$/, "");
      if (!line) continue;
      let rec: JsonlRecord;
      try { rec = JSON.parse(line) as JsonlRecord; } catch { continue; }
      if (rec.type !== "user" && rec.type !== "assistant") continue;
      const text = extractTurnText(rec).trim();
      if (!text) continue;
      if (text.startsWith("<") && text.slice(0, 120).includes(">")) continue;

      buf.push(new Source({
        sessionId,
        filePath: jsonlPath,
        speaker: rec.type,
        turnIdx: idx,
        text: text.slice(0, MAX_TURN_CHARS),
        timestamp: parseTimestamp(rec.timestamp),
      }));
      idx++;

      if (tail !== null && tail > 0 && buf.length > tail) {
        buf.shift();  // bounded deque behavior — keep only last N
      }
    }
  } finally {
    rl.close();
  }

  return buf;
}

// ---------------------------------------------------------------------------
// Context formatting
// ---------------------------------------------------------------------------

function formatConcepts(concepts: Concept[]): string {
  if (concepts.length === 0) return "(none)";
  const lines: string[] = [];
  for (const c of concepts) {
    const state = c.state ? ` state=${c.state}` : "";
    lines.push(`- id="${c.id}" topic="${c.topic}" class=${c.class_} nature=${c.nature}${state}`);
  }
  return lines.join("\n");
}

function formatTurns(turns: Source[]): string {
  if (turns.length === 0) return "(none)";
  const lines: string[] = [];
  for (const t of turns) {
    let snippet = t.text.slice(0, 300).replace(/\n/g, " ");
    if (t.text.length > 300) snippet += " …";
    lines.push(`T${t.turnIdx} [${t.speaker}]: ${snippet}`);
  }
  return lines.join("\n");
}

export async function assembleContext(
  graph: Graph,
  turn: Source,
  recent: Source[],
  embedder: Embedder,
): Promise<{ nearest: Concept[]; ephemerals: Concept[]; recent: Source[] }> {
  let nearest: Concept[] = [];
  if (graph.concepts.size > 0) {
    const turnEmb = await embedder.embed(turn.text.slice(0, 600));
    nearest = graph.nearestConcepts(turnEmb, CONTEXT_K);
  }
  const ephemerals = graph.openEphemeralsInSession(turn.sessionId);
  return {
    nearest,
    ephemerals,
    recent: recent.slice(-RECENT_TURN_N),
  };
}

// ---------------------------------------------------------------------------
// Apply classification to graph
// ---------------------------------------------------------------------------

function readField(o: Record<string, unknown>, key: string): unknown {
  return o[key];
}

export async function applyClassification(
  graph: Graph,
  turn: Source,
  result: ClassifyResult,
  embedder: Embedder,
): Promise<void> {
  graph.addSource(turn);
  if (result.act === "none") return;

  // 1) cites — turn → concept edges + state transitions
  for (const cite of result.cites) {
    if (!cite || typeof cite !== "object") continue;
    const cid = (readField(cite, "concept_id") ?? readField(cite, "id")) as string | undefined;
    if (!cid) continue;
    const c = graph.concepts.get(cid);
    if (!c) continue;
    c.cite(turn.id, turn.speaker);
    const edgeType = ((readField(cite, "edge") ?? readField(cite, "edge_type") ?? "support")) as EdgeType;
    const conf = (readField(cite, "confidence") ?? "medium") as "low" | "medium" | "high";
    try {
      const e = new Edge({
        type: edgeType,
        source: turn.id,
        target: cid,
        establishedAt: turn.id,
        voices: [turn.speaker],
        confidence: conf,
      });
      graph.addEdge(e);
    } catch { continue; }
    if (c.class_ === "ephemeral") {
      if (edgeType === "consume-success" || edgeType === "consume-failure") {
        c.state = "consumed";
        graph.openEphemerals.delete(c.id);
      } else if (edgeType === "retract") {
        c.state = "retracted";
        graph.openEphemerals.delete(c.id);
      }
    }
  }

  // 2) creates — new concepts with cosine dedup
  for (const create of result.creates) {
    if (!create || typeof create !== "object") continue;
    const topicRaw = readField(create, "topic");
    const topic = typeof topicRaw === "string" ? topicRaw.trim() : "";
    if (!topic) continue;

    let class_ = (readField(create, "class") ?? "observation") as ConceptClass;
    if (!VALID_CLASSES.has(class_)) class_ = "observation";
    let nature = (readField(create, "nature") ?? "factual") as ConceptNature;
    if (!VALID_NATURES.has(nature)) nature = "factual";
    let parent = (readField(create, "parent_hint") ?? null) as string | null;
    if (parent && !graph.concepts.has(parent)) parent = null;

    const topicEmb = await embedder.embed(topic);
    const existing = graph.findSimilarConcept(topic, topicEmb);
    if (existing) {
      existing.cite(turn.id, turn.speaker);
      continue;
    }

    let cid = slugifyTopic(topic);
    if (graph.concepts.has(cid)) cid = `${cid}-${graph.concepts.size}`;
    try {
      const nc = new Concept({
        id: cid,
        topic,
        class_,
        nature,
        parent,
        embedding: topicEmb,
      });
      nc.cite(turn.id, turn.speaker);
      graph.addConcept(nc);
    } catch { continue; }
  }

  // 3) concept → concept structural edges
  for (const edge of result.concept_edges) {
    if (!edge || typeof edge !== "object") continue;
    const src = readField(edge, "source") as string | undefined;
    const tgt = readField(edge, "target") as string | undefined;
    const etype = readField(edge, "type") as EdgeType | undefined;
    if (!src || !tgt || !etype) continue;
    if (!graph.concepts.has(src) || !graph.concepts.has(tgt)) continue;
    const conf = (readField(edge, "confidence") ?? "medium") as "low" | "medium" | "high";
    try {
      const e = new Edge({
        type: etype,
        source: src,
        target: tgt,
        establishedAt: turn.id,
        voices: [turn.speaker],
        confidence: conf,
      });
      graph.addEdge(e);
    } catch { continue; }
  }
}

// ---------------------------------------------------------------------------
// Ingest session loop
// ---------------------------------------------------------------------------

export interface IngestStats {
  totalTurns: number;
  llmCalls: number;
  cacheHits: number;
  skippedAlreadyIngested: number;
  actCounts: Record<string, number>;
  staleTransitions: number;
  startedAt: number;
  finishedAt: number;
  elapsedS: number;
}

export interface IngestOptions {
  embedder: Embedder;
  classifier: TurnClassifier;
  onProgress?: (nTurns: number, nConcepts: number, nEdges: number, nLlm: number) => void;
  saveEvery?: number;
  saveTo?: string | null;
  tail?: number | null;
  /**
   * When true, re-classify turns even if already in graph.sources.
   * Used after a PROMPT_VERSION bump to re-extract with the new prompt.
   * Does NOT delete existing concepts/edges — only adds new ones from
   * the fresh classification. Source records are already present so
   * addSource is a no-op; applyClassification runs on the new result.
   */
  force?: boolean;
}

export async function ingestSession(
  graph: Graph,
  jsonlPath: string,
  opts: IngestOptions,
): Promise<IngestStats> {
  const saveEvery = opts.saveEvery ?? 25;
  const turns = await readSessionTurns(jsonlPath, { tail: opts.tail ?? null });
  const processed: Source[] = [];

  const stats: IngestStats = {
    totalTurns: turns.length,
    llmCalls: 0,
    cacheHits: 0,
    skippedAlreadyIngested: 0,
    actCounts: { walk: 0, add: 0, none: 0 },
    staleTransitions: 0,
    startedAt: Date.now() / 1000,
    finishedAt: 0,
    elapsedS: 0,
  };

  const force = opts.force ?? false;
  for (let i = 0; i < turns.length; i++) {
    const turn = turns[i]!;
    // Idempotent skip — re-runs only process new turns.
    // When force=true, skip the check and re-classify everything
    // (used after a PROMPT_VERSION bump).
    if (!force && graph.sources.has(turn.id)) {
      stats.skippedAlreadyIngested++;
      processed.push(turn);
      continue;
    }
    const { nearest, ephemerals, recent } = await assembleContext(
      graph, turn, processed, opts.embedder,
    );
    const contextIds = [...nearest.map(c => c.id), ...ephemerals.map(c => c.id)];
    const recentIds = recent.map(s => s.id);
    const result = await opts.classifier.classify({
      turnText: turn.text,
      speaker: turn.speaker,
      nearestFmt: formatConcepts(nearest),
      ephemeralsFmt: formatConcepts(ephemerals),
      recentFmt: formatTurns(recent),
      contextIds,
      recentIds,
    });
    if (result.wasCached) stats.cacheHits++;
    else stats.llmCalls++;
    stats.actCounts[result.act] = (stats.actCounts[result.act] ?? 0) + 1;

    await applyClassification(graph, turn, result, opts.embedder);
    processed.push(turn);

    if (opts.onProgress) {
      opts.onProgress(i + 1, graph.concepts.size, graph.edges.size, stats.llmCalls);
    }

    if ((i + 1) % saveEvery === 0) {
      opts.embedder.save();
      opts.classifier.save();
      if (opts.saveTo) saveGraph(graph, opts.saveTo);
    }
  }

  opts.embedder.save();
  opts.classifier.save();

  stats.staleTransitions = graph.sweepStaleEphemerals();
  stats.finishedAt = Date.now() / 1000;
  stats.elapsedS = stats.finishedAt - stats.startedAt;
  return stats;
}
