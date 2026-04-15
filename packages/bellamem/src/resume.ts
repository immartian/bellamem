import { Graph } from "./graph.js";
import { Concept } from "./schema.js";
import { audit, redFlags } from "./audit.js";

function byMass(concepts: Concept[]): Concept[] {
  return [...concepts].sort((a, b) => {
    if (a.mass !== b.mass) return b.mass - a.mass;
    return b.sourceRefs.length - a.sourceRefs.length;
  });
}

function byLastTouched(concepts: Concept[]): Concept[] {
  return [...concepts].sort((a, b) => {
    const la = a.lastTouchedAt ?? "";
    const lb = b.lastTouchedAt ?? "";
    if (la < lb) return 1;
    if (la > lb) return -1;
    return 0;
  });
}

export interface ResumeOptions {
  topInvariantMeta?: number;
  topInvariantNorm?: number;
  topInvariantFact?: number;
  topOpenEphemeral?: number;
  topRetracted?: number;
  topDecision?: number;
  topDisputeEdges?: number;
  topRetractEdges?: number;
}

export function resumeText(graph: Graph, opts: ResumeOptions = {}): string {
  const topInvMeta  = opts.topInvariantMeta ?? 12;
  const topInvNorm  = opts.topInvariantNorm ?? 10;
  const topInvFact  = opts.topInvariantFact ?? 6;
  const topOpenEph  = opts.topOpenEphemeral ?? 15;
  const topRetract  = opts.topRetracted ?? 10;
  const topDec      = opts.topDecision ?? 12;
  const topDispute  = opts.topDisputeEdges ?? 10;
  const topRetrE    = opts.topRetractEdges ?? 8;

  const out: string[] = [];
  out.push("# v0.2 graph resume");
  out.push(
    `  ${graph.concepts.size} concepts · ` +
    `${graph.edges.size} edges · ` +
    `${graph.sources.size} sources`,
  );
  out.push("");

  const all = [...graph.concepts.values()];

  const invMeta = byMass(all.filter(c => c.class_ === "invariant" && c.nature === "metaphysical"));
  out.push(`## what the system IS — invariant × metaphysical (${invMeta.length})`);
  for (const c of invMeta.slice(0, topInvMeta)) {
    out.push(`  m=${c.mass.toFixed(2)} [${String(c.sourceRefs.length).padStart(2)}r] ${c.topic}`);
  }
  out.push("");

  const invNorm = byMass(all.filter(c => c.class_ === "invariant" && c.nature === "normative"));
  out.push(`## what we commit to — invariant × normative (${invNorm.length})`);
  for (const c of invNorm.slice(0, topInvNorm)) {
    out.push(`  m=${c.mass.toFixed(2)} [${String(c.sourceRefs.length).padStart(2)}r] ${c.topic}`);
  }
  out.push("");

  const invFact = byMass(all.filter(c => c.class_ === "invariant" && c.nature === "factual"));
  out.push(`## structural facts — invariant × factual (${invFact.length})`);
  for (const c of invFact.slice(0, topInvFact)) {
    out.push(`  m=${c.mass.toFixed(2)} [${String(c.sourceRefs.length).padStart(2)}r] ${c.topic}`);
  }
  out.push("");

  const openEph = byLastTouched(all.filter(c => c.class_ === "ephemeral" && c.state === "open"));
  out.push(`## open work — ephemeral × open (${openEph.length})`);
  for (const c of openEph.slice(0, topOpenEph)) {
    out.push(`  [open] ${c.topic}`);
  }
  out.push("");

  const retracted = byLastTouched(all.filter(c => c.class_ === "ephemeral" && c.state === "retracted"));
  out.push(`## retracted approaches — ephemeral × retracted (${retracted.length})`);
  for (const c of retracted.slice(0, topRetract)) {
    out.push(`  [retracted] ${c.topic}`);
  }
  out.push("");

  const decisions = byLastTouched(all.filter(c => c.class_ === "decision"));
  out.push(`## recent decisions (${decisions.length})`);
  for (const c of decisions.slice(0, topDec)) {
    out.push(`  [decision/${c.nature}] ${c.topic}`);
  }
  out.push("");

  const disputeEdges = [...graph.edges.values()].filter(e => e.type === "dispute");
  if (disputeEdges.length > 0) {
    out.push(`## disputes — ⊥ edges (${disputeEdges.length})`);
    for (const e of disputeEdges.slice(0, topDispute)) {
      const src = graph.concepts.get(e.source);
      const tgt = graph.concepts.get(e.target);
      if (src && tgt) {
        out.push(`  ${src.topic}  ⊥  ${tgt.topic}`);
      } else {
        const tgtT = tgt ? tgt.topic : e.target;
        out.push(`  (turn ${e.source})  ⊥  ${tgtT}`);
      }
    }
    out.push("");
  }

  const retractEdges = [...graph.edges.values()].filter(e => e.type === "retract");
  if (retractEdges.length > 0) {
    out.push(`## speaker retractions — retract edges (${retractEdges.length})`);
    for (const e of retractEdges.slice(0, topRetrE)) {
      const tgt = graph.concepts.get(e.target);
      const tgtT = tgt ? tgt.topic : e.target;
      out.push(`  (turn ${e.source})  retract  ${tgtT}`);
    }
    out.push("");
  }

  const byClass: Record<string, number> = {};
  for (const [k, v] of graph.byClass) byClass[k] = v.size;
  const byNature: Record<string, number> = {};
  for (const [k, v] of graph.byNature) byNature[k] = v.size;
  const ephemeralStates: Record<string, number> = {};
  for (const c of all) {
    if (c.class_ !== "ephemeral") continue;
    const s = c.state ?? "?";
    ephemeralStates[s] = (ephemeralStates[s] ?? 0) + 1;
  }
  const edgeTypes: Record<string, number> = {};
  for (const e of graph.edges.values()) {
    edgeTypes[e.type] = (edgeTypes[e.type] ?? 0) + 1;
  }

  out.push("## stats");
  out.push(`  by_class:  ${fmtDict(byClass)}`);
  out.push(`  by_nature: ${fmtDict(byNature)}`);
  out.push(`  ephemeral_states: ${fmtDict(ephemeralStates)}`);
  out.push(`  edge_types: ${fmtDict(edgeTypes)}`);

  const report = audit(graph);
  const flags = redFlags(report);
  if (flags.length > 0) {
    out.push("");
    out.push("## audit — red flags");
    const marks: Record<string, string> = { soft: "SOFT", hard: "HARD" };
    for (const s of flags) {
      out.push(`  [${marks[s.verdict]}] ${s.name}: ${s.note}`);
    }
  }

  return out.join("\n");
}

function fmtDict(d: Record<string, number>): string {
  const entries = Object.entries(d).map(([k, v]) => `'${k}': ${v}`);
  return `{${entries.join(", ")}}`;
}
