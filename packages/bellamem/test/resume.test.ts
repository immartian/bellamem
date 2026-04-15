import { describe, it, expect } from "vitest";
import { existsSync } from "node:fs";
import { Graph } from "../src/graph.js";
import { Concept, Edge } from "../src/schema.js";
import { resumeText } from "../src/resume.js";
import { replayText } from "../src/replay.js";
import { Source } from "../src/schema.js";
import { loadGraph } from "../src/store.js";

describe("resumeText", () => {
  it("renders all sections in order", () => {
    const g = new Graph();
    g.addConcept(new Concept({ id: "iv", topic: "system IS", class_: "invariant", nature: "metaphysical", mass: 0.9 }));
    g.addConcept(new Concept({ id: "in", topic: "we commit to", class_: "invariant", nature: "normative", mass: 0.8 }));
    g.addConcept(new Concept({ id: "if", topic: "structural fact", class_: "invariant", nature: "factual", mass: 0.7 }));
    g.addConcept(new Concept({ id: "eo", topic: "open work", class_: "ephemeral", nature: "normative" }));
    g.addConcept(new Concept({ id: "er", topic: "retracted work", class_: "ephemeral", nature: "normative", state: "retracted" }));
    g.addConcept(new Concept({ id: "d",  topic: "a decision", class_: "decision", nature: "normative" }));

    const out = resumeText(g);
    const invMetaIdx = out.indexOf("## what the system IS");
    const invNormIdx = out.indexOf("## what we commit to");
    const invFactIdx = out.indexOf("## structural facts");
    const openIdx    = out.indexOf("## open work");
    const retrIdx    = out.indexOf("## retracted approaches");
    const decIdx     = out.indexOf("## recent decisions");
    expect(invMetaIdx).toBeGreaterThan(0);
    expect(invNormIdx).toBeGreaterThan(invMetaIdx);
    expect(invFactIdx).toBeGreaterThan(invNormIdx);
    expect(openIdx).toBeGreaterThan(invFactIdx);
    expect(retrIdx).toBeGreaterThan(openIdx);
    expect(decIdx).toBeGreaterThan(retrIdx);
  });

  it("includes dispute edges when present", () => {
    const g = new Graph();
    g.addConcept(new Concept({ id: "a", topic: "alpha", class_: "observation", nature: "factual" }));
    g.addConcept(new Concept({ id: "b", topic: "beta", class_: "observation", nature: "factual" }));
    g.addEdge(new Edge({ type: "dispute", source: "a", target: "b", establishedAt: "s#0" }));
    const out = resumeText(g);
    expect(out).toContain("## disputes — ⊥ edges");
    expect(out).toContain("alpha  ⊥  beta");
  });
});

describe("resumeText on live .graph/v02.json", () => {
  const livePath = "/media/im3/plus/labX/bellamem/.graph/v02.json";
  it.skipIf(!existsSync(livePath))("renders structural headers without throwing", () => {
    const g = loadGraph(livePath);
    const out = resumeText(g);
    expect(out).toContain("v0.2 graph resume");
    expect(out).toContain("what the system IS — invariant × metaphysical");
    expect(out).toContain("## stats");
    expect(out).toMatch(/## disputes — ⊥ edges \(\d+\)/);
    // Size sanity — the live graph should render hundreds of lines
    expect(out.split("\n").length).toBeGreaterThan(40);
  });
});

describe("replayText session picker", () => {
  it("prefers latest timestamped session over longest untimed", () => {
    const g = new Graph();
    for (let i = 0; i < 50; i++) {
      g.addSource(new Source({ sessionId: "old", filePath: "", speaker: "user", turnIdx: i, text: "x" }));
    }
    g.addSource(new Source({ sessionId: "new", filePath: "", speaker: "user", turnIdx: 0, text: "hi", timestamp: 1_700_000_000 }));
    const out = replayText(g);
    expect(out).toContain("session: new");
  });

  it("falls back to max turn_idx when nothing has a timestamp", () => {
    const g = new Graph();
    for (let i = 0; i < 3; i++) {
      g.addSource(new Source({ sessionId: "a", filePath: "", speaker: "user", turnIdx: i, text: "x" }));
    }
    for (let i = 0; i < 10; i++) {
      g.addSource(new Source({ sessionId: "b", filePath: "", speaker: "user", turnIdx: i, text: "x" }));
    }
    const out = replayText(g);
    expect(out).toContain("session: b");
  });

  it("emits empty-graph message on empty graph", () => {
    const g = new Graph();
    const out = replayText(g);
    expect(out).toContain("empty graph");
  });
});
