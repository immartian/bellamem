import { loadGraph } from "./store.js";
import { graphPathFor, projectRoot } from "./paths.js";
import { resumeText } from "./resume.js";

/**
 * PreToolUse hook body. Reads a JSON envelope from stdin, resolves the
 * target project's v0.2 graph, and writes an advisory pack to stdout.
 * Kept deliberately minimal — the heavy rendering is in resumeText.
 */
export async function runGuard(stdin: string): Promise<string> {
  let env: { cwd?: string } = {};
  try { env = JSON.parse(stdin) as { cwd?: string }; } catch { /* best-effort */ }
  const cwd = env.cwd ?? process.cwd();
  const root = projectRoot(cwd);
  const path = graphPathFor(root);
  const graph = loadGraph(path);
  if (graph.concepts.size === 0) return "";
  return resumeText(graph, {
    topInvariantMeta: 8,
    topInvariantNorm: 8,
    topInvariantFact: 4,
    topOpenEphemeral: 6,
    topRetracted: 4,
    topDecision: 8,
    topDisputeEdges: 8,
    topRetractEdges: 4,
  });
}

async function readStdin(): Promise<string> {
  if (process.stdin.isTTY) return "{}";
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString("utf8");
}

export async function guardMain(): Promise<number> {
  const stdin = await readStdin();
  const out = await runGuard(stdin);
  if (out) process.stdout.write(out);
  return 0;
}
