import { describe, it, expect } from "vitest";
import { existsSync } from "node:fs";
import { Graph } from "../src/graph.js";
import { Concept, Edge, Source } from "../src/schema.js";
import { loadGraph } from "../src/store.js";
import {
  audit, conceptDensity, structuralEdgeRatio,
  massFloorFraction, massSpread, orphanRefs, ephemeralHealth,
} from "../src/audit.js";

function mkC(id: string, mass: number = 0.5, class_: any = "observation"): Concept {
  return new Concept({ id, topic: id, class_, nature: "factual", mass });
}

describe("audit signals — individual", () => {
  it("conceptDensity is concepts/sources", () => {
    const g = new Graph();
    for (let i = 0; i < 10; i++) {
      g.addSource(new Source({ sessionId: "x", filePath: "", speaker: "user", turnIdx: i, text: "t" }));
    }
    for (let i = 0; i < 5; i++) g.addConcept(mkC(`c${i}`));
    expect(conceptDensity(g)).toBeCloseTo(0.5, 5);
  });

  it("structuralEdgeRatio counts only cc edges", () => {
    const g = new Graph();
    g.addConcept(mkC("a"));
    g.addConcept(mkC("b"));
    g.addEdge(new Edge({ type: "cause", source: "a", target: "b", establishedAt: "s#0" }));
    g.addEdge(new Edge({ type: "voice-cross", source: "s#0", target: "a", establishedAt: "s#0" }));
    expect(structuralEdgeRatio(g)).toBeCloseTo(0.5, 5);
  });

  it("massFloorFraction counts m<=0.501", () => {
    const g = new Graph();
    g.addConcept(mkC("a", 0.5));
    g.addConcept(mkC("b", 0.6));
    expect(massFloorFraction(g)).toBeCloseTo(0.5, 5);
  });

  it("massSpread is 0 when all concepts sit in one bucket", () => {
    const g = new Graph();
    g.addConcept(mkC("a", 0.5));
    g.addConcept(mkC("b", 0.5));
    expect(massSpread(g)).toBe(0);
  });

  it("massSpread is >0 when concepts cross buckets", () => {
    const g = new Graph();
    g.addConcept(mkC("a", 0.5));
    g.addConcept(mkC("b", 0.7));
    g.addConcept(mkC("c", 0.9));
    expect(massSpread(g)).toBeGreaterThan(0.3);
  });

  it("orphanRefs counts missing sources", () => {
    const g = new Graph();
    const c = mkC("a");
    c.sourceRefs = ["missing#0", "missing#1"];
    g.addConcept(c);
    expect(orphanRefs(g)).toBe(2);
  });

  it("ephemeralHealth buckets by state", () => {
    const g = new Graph();
    g.addConcept(new Concept({ id: "e1", topic: "a", class_: "ephemeral", nature: "normative", state: "open" }));
    g.addConcept(new Concept({ id: "e2", topic: "b", class_: "ephemeral", nature: "normative", state: "consumed" }));
    g.addConcept(new Concept({ id: "e3", topic: "c", class_: "ephemeral", nature: "normative", state: "retracted" }));
    const h = ephemeralHealth(g);
    expect(h).toEqual({ open: 1, consumed: 1, retracted: 1, stale: 0, none: 0 });
  });
});

describe("audit verdicts", () => {
  it("flags hard on orphan_refs > 0", () => {
    const g = new Graph();
    const c = mkC("a");
    c.sourceRefs = ["missing#0"];
    g.addConcept(c);
    const r = audit(g);
    const orphan = r.signals.find(s => s.name === "orphan_refs")!;
    expect(orphan.verdict).toBe("hard");
  });
});

describe("audit on live .graph/v02.json", () => {
  const livePath = "/media/im3/plus/labX/bellamem/.graph/v02.json";
  it.skipIf(!existsSync(livePath))("returns all-ok on the current graph", () => {
    const g = loadGraph(livePath);
    const r = audit(g);
    for (const s of r.signals) {
      expect(s.verdict, `${s.name}: ${s.note}`).toBe("ok");
    }
    expect(r.nConcepts).toBeGreaterThan(400);
  });
});
