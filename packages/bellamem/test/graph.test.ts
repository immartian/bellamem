import { describe, it, expect } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { Graph, DEDUP_COSINE, cosine } from "../src/graph.js";
import { loadGraph } from "../src/store.js";
import { Concept, Edge, Source } from "../src/schema.js";

function mkConcept(id: string, emb?: Float32Array, class_: "invariant" | "observation" = "observation"): Concept {
  return new Concept({
    id, topic: id, class_, nature: "factual", embedding: emb ?? null,
  });
}

function unit(...vals: number[]): Float32Array {
  const v = Float32Array.from(vals);
  let n = 0;
  for (const x of v) n += x * x;
  const r = Math.sqrt(n);
  for (let i = 0; i < v.length; i++) v[i] /= r;
  return v;
}

describe("cosine", () => {
  it("is 1 for identical unit vectors", () => {
    const a = unit(1, 2, 3);
    expect(cosine(a, a)).toBeCloseTo(1, 6);
  });
  it("is 0 for zero vectors", () => {
    expect(cosine(Float32Array.from([0, 0]), Float32Array.from([1, 1]))).toBe(0);
  });
});

describe("Graph.addEdge merge semantics", () => {
  it("accumulates voices and bumps confidence", () => {
    const g = new Graph();
    g.addEdge(new Edge({
      type: "support", source: "a", target: "b", establishedAt: "s#0",
      voices: ["user"], confidence: "low",
    }));
    const after2 = g.addEdge(new Edge({
      type: "support", source: "a", target: "b", establishedAt: "s#1",
      voices: ["assistant"], confidence: "low",
    }));
    expect(after2.voices).toEqual(["user", "assistant"]);
    expect(after2.confidence).toBe("medium");
    g.addEdge(new Edge({
      type: "support", source: "a", target: "b", establishedAt: "s#2",
      voices: ["third"],
    }));
    expect(g.edges.size).toBe(1);
    const edge = [...g.edges.values()][0]!;
    expect(edge.voices.length).toBe(3);
    expect(edge.confidence).toBe("high");
  });
});

describe("Graph.findSimilarConcept — DEDUP_COSINE=0.78", () => {
  it("returns canonical slug hit first", () => {
    const g = new Graph();
    const c = mkConcept("walker-primitive");
    g.addConcept(c);
    const found = g.findSimilarConcept("walker primitive", unit(1, 0, 0));
    expect(found).toBe(c);
  });
  it("merges above threshold by cosine", () => {
    const g = new Graph();
    g.addConcept(mkConcept("aaa", unit(1, 0.1, 0)));
    const found = g.findSimilarConcept("different slug", unit(1, 0.05, 0));
    expect(found).not.toBeNull();
  });
  it("does NOT merge below threshold", () => {
    const g = new Graph();
    g.addConcept(mkConcept("xxx", unit(1, 0, 0)));
    // Vector far enough to be below 0.78 cosine
    const found = g.findSimilarConcept("yyy", unit(0.5, 1, 0));
    expect(found).toBeNull();
  });
});

describe("Graph.nearestConcepts", () => {
  it("returns top-k by cosine", () => {
    const g = new Graph();
    g.addConcept(mkConcept("a", unit(1, 0, 0)));
    g.addConcept(mkConcept("b", unit(0.9, 0.1, 0)));
    g.addConcept(mkConcept("c", unit(0, 0, 1)));
    const out = g.nearestConcepts(unit(1, 0, 0), 2, 0);
    expect(out.map(c => c.id)).toEqual(["a", "b"]);
  });
});

describe("Graph.sweepStaleEphemerals", () => {
  it("flips old open ephemerals to stale", () => {
    const g = new Graph();
    const now = 1_700_000_000;
    const old = now - 10 * 86400;
    const fresh = now - 1 * 86400;
    const oldSrc = new Source({ sessionId: "x", filePath: "", speaker: "user", turnIdx: 0, text: "t", timestamp: old });
    const freshSrc = new Source({ sessionId: "x", filePath: "", speaker: "user", turnIdx: 1, text: "t", timestamp: fresh });
    g.addSource(oldSrc);
    g.addSource(freshSrc);
    const a = new Concept({ id: "stale-one", topic: "a", class_: "ephemeral", nature: "normative" });
    a.sourceRefs = ["x#0"];
    a.lastTouchedAt = "x#0";
    const b = new Concept({ id: "fresh-one", topic: "b", class_: "ephemeral", nature: "normative" });
    b.sourceRefs = ["x#1"];
    b.lastTouchedAt = "x#1";
    g.addConcept(a); g.addConcept(b);
    const n = g.sweepStaleEphemerals({ nowTs: now });
    expect(n).toBe(1);
    expect(a.state).toBe("stale");
    expect(b.state).toBe("open");
  });
  it("leaves timestamp-less ephemerals alone", () => {
    const g = new Graph();
    const s = new Source({ sessionId: "x", filePath: "", speaker: "user", turnIdx: 0, text: "t", timestamp: null });
    g.addSource(s);
    const c = new Concept({ id: "untimed", topic: "c", class_: "ephemeral", nature: "normative" });
    c.sourceRefs = ["x#0"];
    c.lastTouchedAt = "x#0";
    g.addConcept(c);
    const n = g.sweepStaleEphemerals({ nowTs: Date.now() / 1000 + 1e9 });
    expect(n).toBe(0);
    expect(c.state).toBe("open");
  });
});

describe("Graph JSON round-trip on live .graph/v02.json", () => {
  const livePath = "/media/im3/plus/labX/bellamem/.graph/v02.json";
  it.skipIf(!existsSync(livePath))("loads and re-serializes without drift", () => {
    const graph = loadGraph(livePath);
    expect(graph.concepts.size).toBeGreaterThan(400);

    // Round-trip the non-derivable parts.
    const first = JSON.parse(readFileSync(livePath, "utf8"));
    const out = graph.toJSON();

    // Same counts
    expect(Object.keys(out.sources).length).toBe(Object.keys(first.sources).length);
    expect(Object.keys(out.concepts).length).toBe(Object.keys(first.concepts).length);
    expect(out.edges.length).toBe(first.edges.length);

    // Every concept field survives
    for (const [cid, cdata] of Object.entries(first.concepts) as [string, any][]) {
      const back = out.concepts[cid];
      expect(back).toBeDefined();
      expect(back.topic).toBe(cdata.topic);
      expect(back.class).toBe(cdata.class);
      expect(back.nature).toBe(cdata.nature);
      expect(back.mass).toBe(cdata.mass);
      expect(back.voices).toEqual(cdata.voices);
      expect(back.source_refs).toEqual(cdata.source_refs);
    }
  });
});
