import { Graph } from "./graph.js";
import { Source } from "./schema.js";

function pickSession(graph: Graph, session: string | null): string | null {
  if (session) {
    for (const s of graph.sources.values()) {
      if (s.sessionId === session) return session;
    }
    return null;
  }
  const bySessionTs = new Map<string, number>();
  const bySessionIdx = new Map<string, number>();
  for (const s of graph.sources.values()) {
    if (s.timestamp !== null) {
      const cur = bySessionTs.get(s.sessionId) ?? -Infinity;
      if (s.timestamp > cur) bySessionTs.set(s.sessionId, s.timestamp);
    }
    const curI = bySessionIdx.get(s.sessionId) ?? -1;
    if (s.turnIdx > curI) bySessionIdx.set(s.sessionId, s.turnIdx);
  }
  if (bySessionTs.size > 0) {
    let best = ""; let bestV = -Infinity;
    for (const [k, v] of bySessionTs) if (v > bestV) { bestV = v; best = k; }
    return best;
  }
  if (bySessionIdx.size > 0) {
    let best = ""; let bestV = -1;
    for (const [k, v] of bySessionIdx) if (v > bestV) { bestV = v; best = k; }
    return best;
  }
  return null;
}

function conceptsForTurn(graph: Graph, sourceId: string): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  for (const c of graph.concepts.values()) {
    if (c.sourceRefs.includes(sourceId)) {
      const tag = `${c.class_.slice(0, 3)}/${c.nature.slice(0, 3)}`;
      out.push([tag, c.topic]);
    }
  }
  return out;
}

export interface ReplayOptions {
  session?: string | null;
  sinceTurn?: number;
  maxLines?: number;
  previewChars?: number;
}

export function replayText(graph: Graph, opts: ReplayOptions = {}): string {
  const session = opts.session ?? null;
  const sinceTurn = opts.sinceTurn ?? 0;
  const maxLines = opts.maxLines ?? 120;
  const previewChars = opts.previewChars ?? 140;

  if (graph.sources.size === 0) {
    return "# v0.2 replay\n  empty graph — run `bellamem save` first";
  }

  const sid = pickSession(graph, session);
  if (sid === null) {
    const known = [...new Set([...graph.sources.values()].map(s => s.sessionId))].sort();
    return (
      `# v0.2 replay\n` +
      `  session ${session === null ? "null" : `'${session}'`} not found. ` +
      `Known sessions: [${known.map(s => `'${s}'`).join(", ")}]`
    );
  }

  let turns: Source[] = [];
  for (const s of graph.sources.values()) {
    if (s.sessionId === sid) turns.push(s);
  }
  turns.sort((a, b) => a.turnIdx - b.turnIdx);
  turns = turns.filter(t => t.turnIdx >= sinceTurn);

  const out: string[] = [];
  out.push(`# v0.2 replay (session: ${sid})`);
  out.push(
    `  ${turns.length} turns · ` +
    `${graph.concepts.size} concepts · ` +
    `${graph.edges.size} edges`,
  );
  if (sinceTurn > 0) out.push(`  since_turn: ${sinceTurn}`);
  out.push("");

  const total = turns.length;
  if (total > maxLines) {
    const dropped = total - maxLines;
    turns = turns.slice(-maxLines);
    out.push(`  (dropped ${dropped} head turns — showing tail ${maxLines})`);
    out.push("");
  }

  for (const s of turns) {
    let text = (s.text ?? "").replace(/\n/g, " ").trim();
    if (text.length > previewChars) text = text.slice(0, previewChars - 1) + "…";
    const idx = String(s.turnIdx).padStart(4);
    out.push(`#${idx} [${s.speaker.padEnd(9)}] ${text}`);
    const cites = conceptsForTurn(graph, s.id);
    for (const [tag, topic] of cites.slice(0, 4)) {
      out.push(`          └─ ${tag}  ${topic}`);
    }
    if (cites.length > 4) {
      out.push(`          └─ … +${cites.length - 4} more`);
    }
  }

  out.push("");
  out.push(`— ${turns.length}/${total} turns shown —`);
  return out.join("\n");
}
