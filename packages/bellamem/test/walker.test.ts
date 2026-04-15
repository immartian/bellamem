import { describe, it, expect } from "vitest";
import { existsSync } from "node:fs";
import { Graph } from "../src/graph.js";
import { Concept, Edge } from "../src/schema.js";
import { askText } from "../src/walker.js";
import { loadGraph } from "../src/store.js";

describe("askText — substring+mass scoring", () => {
  it("returns empty pack on empty graph", async () => {
    const g = new Graph();
    const out = await askText(g, "anything");
    expect(out).toContain("empty graph");
  });

  it("ranks by substring when offline", async () => {
    const g = new Graph();
    g.addConcept(new Concept({ id: "walker-primitive", topic: "walker primitive", class_: "invariant", nature: "metaphysical", mass: 0.8 }));
    g.addConcept(new Concept({ id: "herenews-app", topic: "herenews app", class_: "observation", nature: "factual", mass: 0.5 }));
    const out = await askText(g, "walker");
    expect(out).toMatch(/walker primitive/);
    expect(out).toMatch(/## relevant concepts/);
    expect(out).toMatch(/mode=substring\+mass/);
  });

  it("higher mass outranks higher raw substring when comparable", async () => {
    const g = new Graph();
    g.addConcept(new Concept({ id: "a", topic: "walker alpha", class_: "observation", nature: "factual", mass: 0.95 }));
    g.addConcept(new Concept({ id: "b", topic: "walker", class_: "observation", nature: "factual", mass: 0.50 }));
    const out = await askText(g, "walker", { minScore: 0 });
    // both match substring 1.0, but mass breaks the tie. concept 'a' should come first.
    const idxA = out.indexOf("walker alpha");
    const idxB = out.indexOf(" walker\n");
    expect(idxA).toBeGreaterThan(0);
    expect(idxB).toBeGreaterThan(idxA);
  });

  it("surfaces dispute edges in the walk", async () => {
    const g = new Graph();
    g.addConcept(new Concept({ id: "alpha", topic: "alpha", class_: "observation", nature: "factual" }));
    g.addConcept(new Concept({ id: "beta", topic: "beta", class_: "observation", nature: "factual" }));
    g.addEdge(new Edge({ type: "dispute", source: "alpha", target: "beta", establishedAt: "s#0" }));
    const out = await askText(g, "alpha");
    expect(out).toContain("⊥ disputes");
    expect(out).toContain("alpha");
    expect(out).toContain("beta");
  });

  it("classFilter restricts seeds", async () => {
    const g = new Graph();
    g.addConcept(new Concept({ id: "a", topic: "walker alpha", class_: "invariant", nature: "metaphysical" }));
    g.addConcept(new Concept({ id: "b", topic: "walker beta", class_: "ephemeral", nature: "normative" }));
    const out = await askText(g, "walker", { classFilter: "invariant" });
    expect(out).toContain("walker alpha");
    expect(out).not.toContain("walker beta");
  });
});

describe("askText on live .graph/v02.json", () => {
  const livePath = "/media/im3/plus/labX/bellamem/.graph/v02.json";
  it.skipIf(!existsSync(livePath))("returns seeds for 'walker'", async () => {
    const g = loadGraph(livePath);
    const out = await askText(g, "walker");
    expect(out).toMatch(/## relevant concepts/);
    expect(out).toMatch(/walker/);
  });
});
