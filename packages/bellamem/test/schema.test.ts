import { describe, it, expect } from "vitest";
import {
  Concept, Edge, Source, slugifyTopic, logit, sigmoid,
  MASS_DELTA_NEW_VOICE, MASS_DELTA_REPEAT_VOICE,
} from "../src/schema.js";

describe("slugifyTopic", () => {
  it("canonicalizes topic strings stably", () => {
    expect(slugifyTopic("Walker Primitive")).toBe("walker-primitive");
    expect(slugifyTopic("  leading/trailing-__??")).toBe("leading-trailing");
    expect(slugifyTopic("!!!")).toBe("unnamed");
    expect(slugifyTopic("")).toBe("unnamed");
  });
  it("truncates to 60 chars", () => {
    const long = "a".repeat(200);
    expect(slugifyTopic(long).length).toBe(60);
  });
  it("matches known Python slug form for typical topics", () => {
    expect(slugifyTopic("Walker Primitive Test Concept"))
      .toBe("walker-primitive-test-concept");
    expect(slugifyTopic("impact of embedder choice on Jaccard scores"))
      .toBe("impact-of-embedder-choice-on-jaccard-scores");
  });
});

describe("logit / sigmoid", () => {
  it("are inverses for mid-range p", () => {
    for (const p of [0.1, 0.3, 0.5, 0.7, 0.9]) {
      expect(sigmoid(logit(p))).toBeCloseTo(p, 5);
    }
  });
  it("clamps extremes without NaN", () => {
    expect(sigmoid(logit(0))).toBeLessThan(1e-5);
    expect(sigmoid(logit(1))).toBeGreaterThan(1 - 1e-5);
    expect(Number.isFinite(logit(0))).toBe(true);
    expect(Number.isFinite(logit(1))).toBe(true);
  });
});

describe("Concept.cite — R1 accumulation", () => {
  const mk = () => new Concept({
    id: "c", topic: "c", class_: "observation", nature: "factual",
  });

  it("first citation with speaker bumps mass by NEW_VOICE delta", () => {
    const c = mk();
    expect(c.mass).toBe(0.5);
    c.cite("s#0", "user");
    expect(c.voices).toEqual(["user"]);
    expect(c.mass).toBeCloseTo(sigmoid(MASS_DELTA_NEW_VOICE), 5);
  });

  it("second cite by the same speaker uses REPEAT delta", () => {
    const c = mk();
    c.cite("s#0", "user");
    const afterNew = c.mass;
    c.cite("s#1", "user");
    expect(c.voices).toEqual(["user"]);
    const expectedLogits = logit(afterNew) + MASS_DELTA_REPEAT_VOICE;
    expect(c.mass).toBeCloseTo(sigmoid(expectedLogits), 5);
  });

  it("new speaker triggers NEW delta even after repeats", () => {
    const c = mk();
    c.cite("s#0", "user");
    c.cite("s#1", "user");
    const before = c.mass;
    c.cite("s#2", "assistant");
    expect(c.voices).toEqual(["user", "assistant"]);
    expect(c.mass).toBeCloseTo(sigmoid(logit(before) + MASS_DELTA_NEW_VOICE), 5);
  });

  it("is idempotent on duplicate source_id", () => {
    const c = mk();
    c.cite("s#0", "user");
    const before = c.mass;
    c.cite("s#0", "user");
    expect(c.mass).toBe(before);
    expect(c.sourceRefs.length).toBe(1);
  });

  it("empty speaker still records source_ref but does not move mass", () => {
    const c = mk();
    c.cite("s#0", "");
    expect(c.sourceRefs).toEqual(["s#0"]);
    expect(c.voices).toEqual([]);
    expect(c.mass).toBe(0.5);
  });

  it("first_voiced_at/last_touched_at track correctly", () => {
    const c = mk();
    c.cite("s#0", "user");
    c.cite("s#5", "assistant");
    expect(c.firstVoicedAt).toBe("s#0");
    expect(c.lastTouchedAt).toBe("s#5");
  });
});

describe("Concept validation", () => {
  it("rejects invalid class/nature/state", () => {
    expect(() => new Concept({
      id: "x", topic: "x", class_: "bogus" as any, nature: "factual",
    })).toThrow();
    expect(() => new Concept({
      id: "x", topic: "x", class_: "observation", nature: "bogus" as any,
    })).toThrow();
    expect(() => new Concept({
      id: "x", topic: "x", class_: "observation", nature: "factual",
      state: "open",
    })).toThrow(/ephemeral/);
  });
  it("ephemeral defaults state to open", () => {
    const c = new Concept({
      id: "x", topic: "x", class_: "ephemeral", nature: "normative",
    });
    expect(c.state).toBe("open");
  });
});

describe("Concept JSON round-trip", () => {
  it("preserves fields byte-for-byte", () => {
    const c = new Concept({
      id: "walker-primitive", topic: "walker primitive",
      class_: "invariant", nature: "metaphysical",
      mass: 0.72, voices: ["user", "assistant"],
      sourceRefs: ["s#0", "s#5"],
      firstVoicedAt: "s#0", lastTouchedAt: "s#5",
    });
    const j = c.toJSON();
    const back = Concept.fromJSON(j);
    expect(back.toJSON()).toEqual(j);
  });
});

describe("Edge id determinism", () => {
  it("is deterministic across instances", () => {
    const a = new Edge({
      type: "support", source: "c1", target: "c2",
      establishedAt: "s#0",
    });
    const b = new Edge({
      type: "support", source: "c1", target: "c2",
      establishedAt: "s#99", voices: ["different"],
    });
    expect(a.id).toBe(b.id);
    expect(a.id.length).toBe(16);
  });
  it("changes when type/source/target changes", () => {
    const a = new Edge({ type: "support", source: "c1", target: "c2", establishedAt: "s#0" });
    const b = new Edge({ type: "dispute", source: "c1", target: "c2", establishedAt: "s#0" });
    expect(a.id).not.toBe(b.id);
  });
  it("matches Python sha256 for a known triple", () => {
    // Python: hashlib.sha256(b"support|c1|c2").hexdigest()[:16] = "6b0408a7c5f4a60c"
    const e = new Edge({ type: "support", source: "c1", target: "c2", establishedAt: "s#0" });
    expect(e.id).toBe("6b0408a7c5f4a60c");
  });
  it("rejects invalid edge type", () => {
    expect(() => new Edge({
      type: "bogus" as any, source: "a", target: "b", establishedAt: "s#0",
    })).toThrow();
  });
});

describe("Source", () => {
  it("has deterministic id", () => {
    const s = new Source({
      sessionId: "abcd1234", filePath: "/x.jsonl",
      speaker: "user", turnIdx: 42, text: "hi",
    });
    expect(s.id).toBe("abcd1234#42");
  });
  it("truncates text_preview to 200 on serialize", () => {
    const s = new Source({
      sessionId: "x", filePath: "/x",
      speaker: "user", turnIdx: 0,
      text: "a".repeat(500),
    });
    expect(s.toJSON().text_preview.length).toBe(200);
  });
});
