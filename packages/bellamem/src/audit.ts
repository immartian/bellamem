import { Graph } from "./graph.js";
import { EdgeType } from "./schema.js";

const DENSITY_SOFT    = 0.60;
const DENSITY_HARD    = 0.85;
const STRUCTURAL_SOFT = 0.10;
const STRUCTURAL_HARD = 0.04;
const FLOOR_SOFT      = 0.20;
const FLOOR_HARD      = 0.50;
const SPREAD_SOFT     = 0.20;
const SPREAD_HARD     = 0.08;

const STATIC_EDGE_TYPES: ReadonlySet<EdgeType> = new Set([
  "cause", "elaborate", "dispute", "support",
]);

export type Verdict = "ok" | "soft" | "hard";

export interface Signal {
  name: string;
  value: number;
  verdict: Verdict;
  note: string;
}

export interface AuditReport {
  nConcepts: number;
  nEdges: number;
  nSources: number;
  signals: Signal[];
  ephemeral: Record<string, number>;
}

export function massSpread(graph: Graph): number {
  if (graph.concepts.size === 0) return 0;
  const buckets = new Array<number>(10).fill(0);
  for (const c of graph.concepts.values()) {
    let idx = Math.floor((c.mass - 0.5) * 20);
    if (idx < 0) idx = 0;
    if (idx > 9) idx = 9;
    buckets[idx]!++;
  }
  const total = buckets.reduce((a, b) => a + b, 0);
  if (total === 0) return 0;
  const probs: number[] = [];
  for (const b of buckets) if (b > 0) probs.push(b / total);
  if (probs.length <= 1) return 0;
  let h = 0;
  for (const p of probs) h -= p * Math.log(p);
  return h / Math.log(10);
}

export function conceptDensity(graph: Graph): number {
  if (graph.sources.size === 0) return 0;
  return graph.concepts.size / graph.sources.size;
}

export function structuralEdgeRatio(graph: Graph): number {
  if (graph.edges.size === 0) return 0;
  let structural = 0;
  for (const e of graph.edges.values()) {
    if (!STATIC_EDGE_TYPES.has(e.type)) continue;
    if (!graph.concepts.has(e.source)) continue;
    if (!graph.concepts.has(e.target)) continue;
    structural++;
  }
  return structural / graph.edges.size;
}

export function massFloorFraction(graph: Graph): number {
  if (graph.concepts.size === 0) return 0;
  let atFloor = 0;
  for (const c of graph.concepts.values()) {
    if (c.mass <= 0.501) atFloor++;
  }
  return atFloor / graph.concepts.size;
}

export function orphanRefs(graph: Graph): number {
  let missing = 0;
  for (const c of graph.concepts.values()) {
    for (const sr of c.sourceRefs) {
      if (!graph.sources.has(sr)) missing++;
    }
  }
  return missing;
}

export function ephemeralHealth(graph: Graph): Record<string, number> {
  const out: Record<string, number> = {
    open: 0, consumed: 0, retracted: 0, stale: 0, none: 0,
  };
  for (const c of graph.concepts.values()) {
    if (c.class_ !== "ephemeral") continue;
    const key = c.state && out[c.state] !== undefined ? c.state : "none";
    out[key]!++;
  }
  return out;
}

function verdictHighIsBad(v: number, soft: number, hard: number): Verdict {
  if (v >= hard) return "hard";
  if (v >= soft) return "soft";
  return "ok";
}

function verdictHighIsGood(v: number, soft: number, hard: number): Verdict {
  if (v <= hard) return "hard";
  if (v <= soft) return "soft";
  return "ok";
}

export function audit(graph: Graph): AuditReport {
  const density = conceptDensity(graph);
  const structural = structuralEdgeRatio(graph);
  const floor = massFloorFraction(graph);
  const spread = massSpread(graph);
  const orphans = orphanRefs(graph);

  const pct = (v: number) => `${Math.round(v * 100)}%`;

  const signals: Signal[] = [
    {
      name: "concept_density",
      value: density,
      verdict: verdictHighIsBad(density, DENSITY_SOFT, DENSITY_HARD),
      note: `${density.toFixed(2)} concepts per source ` +
            `(${graph.concepts.size}/${graph.sources.size}) — ` +
            `high values mean the extractor splits ideas into sub-attributes`,
    },
    {
      name: "structural_edge_ratio",
      value: structural,
      verdict: verdictHighIsGood(structural, STRUCTURAL_SOFT, STRUCTURAL_HARD),
      note: `${pct(structural)} of edges are concept↔concept — ` +
            `below 10% reads as bipartite transcript, not a concept graph`,
    },
    {
      name: "mass_floor_fraction",
      value: floor,
      verdict: verdictHighIsBad(floor, FLOOR_SOFT, FLOOR_HARD),
      note: `${pct(floor)} of concepts at m=0.5 floor — ` +
            `high values mean R1 never fired ` +
            `(run \`bellamem.proto rebuild-mass\`)`,
    },
    {
      name: "mass_spread",
      value: spread,
      verdict: verdictHighIsGood(spread, SPREAD_SOFT, SPREAD_HARD),
      note: `mass-bucket entropy ${spread.toFixed(2)} — ` +
            `low values mean ratification isn't discriminating ` +
            `(concepts cluster in one or two mass buckets)`,
    },
    {
      name: "orphan_refs",
      value: orphans,
      verdict: orphans > 0 ? "hard" : "ok",
      note: `${orphans} citations point at missing sources — should always be 0`,
    },
  ];

  return {
    nConcepts: graph.concepts.size,
    nEdges: graph.edges.size,
    nSources: graph.sources.size,
    signals,
    ephemeral: ephemeralHealth(graph),
  };
}

export function redFlags(report: AuditReport): Signal[] {
  return report.signals.filter((s) => s.verdict !== "ok");
}

export function anyHard(report: AuditReport): boolean {
  return report.signals.some((s) => s.verdict === "hard");
}

export function formatAudit(report: AuditReport): string {
  const lines: string[] = [
    `## audit signals   (${report.nConcepts}c · ${report.nEdges}e · ${report.nSources}s)`,
  ];
  const marks: Record<Verdict, string> = { ok: "  ok", soft: "SOFT", hard: "HARD" };
  for (const s of report.signals) {
    lines.push(`  [${marks[s.verdict]}] ${s.name.padEnd(22)} ${s.note}`);
  }
  const eh = report.ephemeral;
  lines.push(
    `  ephemerals: open=${eh.open} consumed=${eh.consumed} ` +
    `retracted=${eh.retracted} stale=${eh.stale}`,
  );
  return lines.join("\n");
}
