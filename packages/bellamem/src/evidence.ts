import { createReadStream } from "node:fs";
import { createInterface } from "node:readline";
import { Graph } from "./graph.js";
import { Concept, Source, logit, sigmoid, MASS_DELTA_NEW_VOICE, MASS_DELTA_REPEAT_VOICE } from "./schema.js";

interface FullTurnResult {
  text: string;
  lineNumber: number;  // 1-based line in the jsonl file
}

/**
 * Read the FULL text of a specific speaker turn from a session jsonl,
 * plus the 1-based line number in the file for editor jump-to.
 */
async function readFullTurn(filePath: string, turnIdx: number): Promise<FullTurnResult | null> {
  const rl = createInterface({
    input: createReadStream(filePath, { encoding: "utf8" }),
    crlfDelay: Infinity,
  });
  let idx = 0;
  let lineNo = 0;
  try {
    for await (const raw of rl) {
      lineNo++;
      if (!raw) continue;
      let rec: any;
      try { rec = JSON.parse(raw); } catch { continue; }
      if (rec.type !== "user" && rec.type !== "assistant") continue;
      const content = rec.message?.content;
      let text = "";
      if (typeof content === "string") text = content;
      else if (Array.isArray(content)) {
        text = content
          .filter((b: any) => b?.type === "text")
          .map((b: any) => b.text ?? "")
          .join("\n");
      }
      text = text.trim();
      if (!text) continue;
      if (text.startsWith("<") && text.slice(0, 120).includes(">")) continue;
      if (idx === turnIdx) return { text, lineNumber: lineNo };
      idx++;
    }
  } finally {
    rl.close();
  }
  return null;
}

export interface EvidenceEntry {
  source_id: string;
  speaker: string;
  timestamp: number | null;
  file_path: string;
  turn_idx: number;
  line_number: number;  // 1-based line in the jsonl file
  mass_before: number;
  mass_after: number;
  voice_is_new: boolean;
  text: string;         // full text from the jsonl, not truncated
  is_birth: boolean;     // true if this is first_voiced_at
}

export interface EvidenceResult {
  concept_id: string;
  topic: string;
  class: string;
  nature: string;
  state: string | null;
  mass: number;
  voices: string[];
  entries: EvidenceEntry[];
}

/**
 * Build the full provenance chain for a concept: every turn that
 * cited it, with the complete original text read back from the jsonl,
 * R1 mass deltas, and birth marker.
 */
export async function gatherEvidence(
  graph: Graph,
  concept: Concept,
): Promise<EvidenceResult> {
  // Replay mass to get per-citation deltas.
  const voices: string[] = [];
  let mass = 0.5;
  const entries: EvidenceEntry[] = [];

  for (const sr of concept.sourceRefs) {
    const src = graph.sources.get(sr);
    const speaker = src?.speaker ?? "";
    const before = mass;
    let isNew = false;

    if (speaker) {
      isNew = !voices.includes(speaker);
      const delta = isNew ? MASS_DELTA_NEW_VOICE : MASS_DELTA_REPEAT_VOICE;
      if (isNew) voices.push(speaker);
      mass = sigmoid(logit(mass) + delta);
    }

    // Read full text + line number from the original jsonl.
    let fullText = "(source file not found)";
    let lineNumber = 0;
    if (src) {
      const read = await readFullTurn(src.filePath, src.turnIdx);
      if (read) {
        fullText = read.text;
        lineNumber = read.lineNumber;
      } else {
        fullText = src.text;
      }
    }

    entries.push({
      source_id: sr,
      speaker,
      timestamp: src?.timestamp ?? null,
      file_path: src?.filePath ?? "",
      turn_idx: src?.turnIdx ?? -1,
      line_number: lineNumber,
      mass_before: before,
      mass_after: mass,
      voice_is_new: isNew,
      text: fullText,
      is_birth: sr === concept.firstVoicedAt,
    });
  }

  return {
    concept_id: concept.id,
    topic: concept.topic,
    class: concept.class_,
    nature: concept.nature,
    state: concept.state,
    mass: concept.mass,
    voices: [...concept.voices],
    entries,
  };
}

/**
 * Format evidence for terminal output.
 */
export function formatEvidence(ev: EvidenceResult, maxTextChars: number = 500): string {
  const lines: string[] = [];
  lines.push(`# evidence: ${ev.topic}`);
  lines.push(`  ${ev.class}/${ev.nature}${ev.state ? " · " + ev.state : ""}  m=${ev.mass.toFixed(3)}  voices=${ev.voices.join(",")}`);
  lines.push(`  ${ev.entries.length} citations`);
  lines.push("");

  for (const e of ev.entries) {
    const when = e.timestamp
      ? new Date(e.timestamp * 1000).toISOString().replace("T", " ").slice(0, 19)
      : "untimed";
    const birth = e.is_birth ? " ★ BIRTH" : "";
    const delta = e.mass_after - e.mass_before;
    const deltaStr = delta > 0 ? ` (+${delta.toFixed(3)})` : "";
    const loc = e.line_number > 0 ? `  line ${e.line_number}` : "";
    lines.push(`── ${e.source_id} [${e.speaker}] ${when}${birth}`);
    lines.push(`   ${e.file_path}:${e.line_number}`);
    lines.push(`   mass ${e.mass_before.toFixed(3)} → ${e.mass_after.toFixed(3)}${deltaStr}${e.voice_is_new ? " (new voice)" : ""}`);
    // Show the turn text, indented and truncated.
    const text = e.text.length > maxTextChars
      ? e.text.slice(0, maxTextChars - 1) + "…"
      : e.text;
    for (const line of text.split("\n")) {
      lines.push(`   │ ${line}`);
    }
    lines.push("");
  }

  return lines.join("\n");
}
