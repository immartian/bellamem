import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync, unlinkSync } from "node:fs";
import { dirname, join } from "node:path";
import { createHash, randomBytes } from "node:crypto";
import OpenAI from "openai";

export const PROMPT_VERSION = "v3";
export const LLM_MODEL_DEFAULT = "gpt-4o-mini";
export const EMBED_MODEL_DEFAULT = "text-embedding-3-small";

// ---------------------------------------------------------------------------
// Prompts — frozen with PROMPT_VERSION. Any change requires bumping the
// version string, which invalidates the cache.
// ---------------------------------------------------------------------------

export const SYSTEM_PROMPT = `You watch a developer/AI conversation and maintain a project concept graph for the bellamem project.

For each new turn, decide what the turn does to the graph.

A concept is a distinct IDEA identified by a short topic phrase (3-10 words)
and classified on two axes:

class (temporal profile):
  - invariant: time-stable principles or structural facts; never expire
  - decision: revisable commitments ("we'll ship X before Y")
  - observation: factual claims about current state ("the bench scored N")
  - ephemeral: time-bound plans with a state machine (open/consumed/retracted/stale)

nature (epistemic type):
  - factual: measurable or checkable against reality
  - normative: commitments about how we SHOULD act or build
  - metaphysical: claims about what the system or its concepts ARE

The context you receive:
  - nearest_concepts: existing concepts in the graph relevant to this turn
  - open_ephemerals: ephemeral plans in this session still in "open" state
  - recent_turns: the last few turns for anaphora
  - current_turn: the turn to classify

Output strict JSON with:
  - act: "walk" | "add" | "none"
    "walk" = turn reacts to existing concepts (ratification, dispute, consume, retract)
    "add"  = turn introduces a genuinely new IDEA
    "none" = procedural (question, meta-authorization, acknowledgment, tool notification)

  - cites: list of {"concept_id": "<id from nearest/ephemerals>", "edge": "<edge_type>"}
    These are TURN → concept edges (how this turn touched an existing concept).
    edge types: voice-cross | support | dispute | retract | consume-success | consume-failure

  - creates: list of {"topic": "<3-8 word phrase>", "class": "<class>", "nature": "<nature>", "parent_hint": "<concept_id|null>"}
    Only create concepts for genuinely new IDEAS. Prefer citing existing concepts.

  - concept_edges: list of {"source": "<concept_id>", "target": "<concept_id>", "type": "<edge_type>", "confidence": "low|medium|high"}
    CONCEPT → concept structural relationships. First-class citizens — always
    look for these when a turn relates two ideas.
    edge types: cause | elaborate | dispute | support
    Use these whenever the turn establishes a structural link between two concepts:
      - cause:     "X because Y"       / "Y led to X"         / "the reason for X is Y"
      - elaborate: "X is a case of Y"  / "X specializes Y"    / "X refines Y"
      - dispute:   "X contradicts Y"   / "X is wrong, Y is right"
      - support:   "X confirms Y"      / "X and Y agree"  (use sparingly — weakest signal)
    Source and target must both be concept_ids, either from nearest_concepts
    or from concepts you're creating in this same turn.

ONE CONCEPT PER IDEA — do NOT split into attributes:
  Bad:  creates=[{topic: "shape encoding for concept map"},
                 {topic: "fill color encoding for concept map"},
                 {topic: "label positioning for concept map"},
                 {topic: "turn hubs representation"},
                 {topic: "edges representation in concept map"}]
  Good: creates=[{topic: "concept map visual vocabulary", class: "invariant", nature: "metaphysical"}]
        (one node capturing the whole discussion; sub-attributes are prose in the turn text,
         not separate concepts. Only split if each attribute will plausibly be cited again
         independently later.)

EXTRACTION RULES:
- Questions → act=none
- Meta-authorization ("do whatever", "sure go", "I trust your call") → act=none
- Short acknowledgments ("thanks", "ok", "ya") → act=walk with voice-cross IF the prior
  turn had a concrete proposal; otherwise act=none
- Retraction markers ("wait — hold on", "scratch that", "actually on reflection")
  → act=walk with retract cite
- Tool notifications, shell output, task-notification blocks → act=none

CONCEPT HYGIENE:
- Topic phrases: noun-phrase form, concise, canonical, re-usable across sessions
- Prefer merging near-variant topics — if "walker primitive" exists, don't create
  "walker abstraction", "walker protocol", "walker interface". Use the existing one.
- EXCEPTION: numbered sequences are DISTINCT concepts. "Spiral 7" is NOT a variant
  of "Spiral 6". "Phase 2" is NOT "Phase 1". "v0.3" is NOT "v0.2". Each numbered
  item in a sequence tracks different work/state/decisions — always create a new
  concept, never merge into the prior number.
- Prefer zero creates over one create. Prefer one create over many.
- When in doubt between walk and none, prefer none

EDGE HYGIENE:
- Don't default to "support" for cites. Support is the weakest edge and should be
  rare — use voice-cross for neutral mention, or prefer a more informative edge type.
- Always scan for cause / elaborate / dispute between the concepts you touch. Those
  are the structural edges the graph actually needs.
- concept_edges is first-class — populate it aggressively whenever two concepts
  relate, not just as an afterthought.

Return ONLY valid JSON.
`;

export const USER_TEMPLATE = `### nearest_concepts
{nearest}

### open_ephemerals
{ephemerals}

### recent_turns
{recent}

### current_turn
speaker: {speaker}
text: """
{text}
"""

Output JSON only.`;

// ---------------------------------------------------------------------------
// Atomic small-file write helper
// ---------------------------------------------------------------------------

function atomicWrite(target: string, data: string): void {
  const parent = dirname(target);
  mkdirSync(parent, { recursive: true });
  const tmp = join(parent, `.${randomBytes(6).toString("hex")}.tmp`);
  try {
    writeFileSync(tmp, data, "utf8");
    renameSync(tmp, target);
  } catch (err) {
    try { unlinkSync(tmp); } catch {}
    throw err;
  }
}

// ---------------------------------------------------------------------------
// Embedder
// ---------------------------------------------------------------------------

export interface EmbedderOptions {
  cachePath: string;
  model?: string;
  client?: OpenAI;
}

export class Embedder {
  readonly cachePath: string;
  readonly model: string;
  private cache: Record<string, number[]> = {};
  private dirty = false;
  private client: OpenAI | null;

  constructor(opts: EmbedderOptions) {
    this.cachePath = opts.cachePath;
    this.model = opts.model ?? EMBED_MODEL_DEFAULT;
    this.client = opts.client ?? null;
    this.loadCache();
  }

  private loadCache(): void {
    if (!existsSync(this.cachePath)) return;
    try {
      this.cache = JSON.parse(readFileSync(this.cachePath, "utf8"));
    } catch {
      this.cache = {};
    }
  }

  save(): void {
    if (!this.dirty) return;
    atomicWrite(this.cachePath, JSON.stringify(this.cache));
    this.dirty = false;
  }

  private ensureClient(): OpenAI {
    if (!this.client) this.client = new OpenAI();
    return this.client;
  }

  async embed(text: string): Promise<Float32Array> {
    const key = createHash("sha256").update(text).digest("hex");
    const hit = this.cache[key];
    if (hit) return Float32Array.from(hit);
    const client = this.ensureClient();
    const resp = await client.embeddings.create({
      model: this.model,
      input: text.slice(0, 8000),
    });
    const v = resp.data[0]!.embedding;
    this.cache[key] = v;
    this.dirty = true;
    return Float32Array.from(v);
  }
}

// ---------------------------------------------------------------------------
// TurnClassifier
// ---------------------------------------------------------------------------

export interface ClassifyResultJSON {
  act: "walk" | "add" | "none";
  cites: Array<Record<string, unknown>>;
  creates: Array<Record<string, unknown>>;
  concept_edges: Array<Record<string, unknown>>;
}

export interface ClassifyResult extends ClassifyResultJSON {
  wasCached: boolean;
}

function fromRaw(data: Partial<ClassifyResultJSON>, wasCached: boolean): ClassifyResult {
  return {
    act: (data.act ?? "none") as ClassifyResult["act"],
    cites: data.cites ?? [],
    creates: data.creates ?? [],
    concept_edges: data.concept_edges ?? [],
    wasCached,
  };
}

export interface ClassifierOptions {
  cachePath: string;
  model?: string;
  client?: OpenAI;
}

export interface ClassifyInput {
  turnText: string;
  speaker: string;
  nearestFmt: string;
  ephemeralsFmt: string;
  recentFmt: string;
  contextIds: string[];
  recentIds: string[];
}

export class TurnClassifier {
  readonly cachePath: string;
  readonly model: string;
  private cache: Record<string, ClassifyResultJSON> = {};
  private dirty = false;
  private client: OpenAI | null;

  constructor(opts: ClassifierOptions) {
    this.cachePath = opts.cachePath;
    this.model = opts.model ?? LLM_MODEL_DEFAULT;
    this.client = opts.client ?? null;
    this.loadCache();
  }

  private loadCache(): void {
    if (!existsSync(this.cachePath)) return;
    try {
      this.cache = JSON.parse(readFileSync(this.cachePath, "utf8"));
    } catch {
      this.cache = {};
    }
  }

  save(): void {
    if (!this.dirty) return;
    atomicWrite(this.cachePath, JSON.stringify(this.cache));
    this.dirty = false;
  }

  private ensureClient(): OpenAI {
    if (!this.client) this.client = new OpenAI();
    return this.client;
  }

  static cacheKey(turnText: string, contextIds: string[], recentIds: string[]): string {
    const h = createHash("sha256");
    h.update(PROMPT_VERSION);
    h.update("\x00");
    h.update(turnText);
    h.update("\x00");
    h.update([...contextIds].sort().join(","));
    h.update("\x00");
    h.update(recentIds.join(","));
    return h.digest("hex");
  }

  async classify(input: ClassifyInput): Promise<ClassifyResult> {
    const key = TurnClassifier.cacheKey(
      input.turnText, input.contextIds, input.recentIds,
    );
    const hit = this.cache[key];
    if (hit) return fromRaw(hit, true);

    const user = USER_TEMPLATE
      .replace("{nearest}", input.nearestFmt)
      .replace("{ephemerals}", input.ephemeralsFmt)
      .replace("{recent}", input.recentFmt)
      .replace("{speaker}", input.speaker)
      .replace("{text}", input.turnText);

    try {
      const client = this.ensureClient();
      const resp = await client.chat.completions.create({
        model: this.model,
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: user },
        ],
        response_format: { type: "json_object" },
        temperature: 0.0,
      });
      const raw = resp.choices[0]?.message?.content ?? "{}";
      const parsed = JSON.parse(raw) as ClassifyResultJSON;
      // Persist and return — only real classifications are cached.
      this.cache[key] = parsed;
      this.dirty = true;
      return fromRaw(parsed, false);
    } catch (e) {
      // Fail closed: synthetic none, NEVER cached.
      console.error(`  [classify error] ${(e as Error).message}`);
      return fromRaw(
        { act: "none", cites: [], creates: [], concept_edges: [] },
        false,
      );
    }
  }
}
