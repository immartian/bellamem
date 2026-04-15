import { Command } from "commander";
import { existsSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import * as lockfile from "proper-lockfile";
import { loadEnv } from "./env.js";
import { projectRoot, graphPathFor, projectDirFor } from "./paths.js";
import { loadGraph, saveGraph } from "./store.js";
import { Embedder, TurnClassifier } from "./clients.js";
import { ingestSession } from "./ingest.js";
import { resumeText } from "./resume.js";
import { askText } from "./walker.js";
import { audit, formatAudit } from "./audit.js";
import { replayText } from "./replay.js";
import { ConceptClass } from "./schema.js";

function scratchDir(): string {
  const d = join(tmpdir(), "bellamem-proto-tree");
  return d;
}

function cachePaths(): { embed: string; llm: string } {
  const s = scratchDir();
  return {
    embed: join(s, "proto-embed-cache.json"),
    llm: join(s, "proto-llm-cache.json"),
  };
}

function resolveSessionJsonl(root: string): string | null {
  // Find the most recently modified .jsonl under ~/.claude/projects/<encoded>/
  const encoded = projectDirFor(resolve(root));
  if (!existsSync(encoded)) return null;
  let best: { path: string; mtime: number } | null = null;
  for (const name of readdirSync(encoded)) {
    if (!name.endsWith(".jsonl")) continue;
    const p = join(encoded, name);
    try {
      const st = statSync(p);
      if (!best || st.mtimeMs > best.mtime) best = { path: p, mtime: st.mtimeMs };
    } catch { /* skip */ }
  }
  return best?.path ?? null;
}

export function buildProgram(): Command {
  const program = new Command();
  program
    .name("bellamem")
    .description("Local accumulating memory for LLM coding agents")
    .version("0.3.0-alpha.0");

  program
    .command("resume")
    .description("Render the typed graph resume")
    .option("--graph <path>", "path to v0.2 graph JSON")
    .action((opts: { graph?: string }) => {
      loadEnv();
      const root = projectRoot();
      const path = opts.graph ?? graphPathFor(root);
      const graph = loadGraph(path);
      if (graph.concepts.size === 0) {
        console.error(`warning: graph is empty (${path} not found or has no concepts)`);
        process.exit(1);
      }
      console.log(resumeText(graph));
    });

  program
    .command("save")
    .description("Ingest the current session into the graph")
    .option("--tail <n>", "only ingest the last N turns", (v) => parseInt(v, 10))
    .option("--graph <path>", "path to v0.2 graph JSON")
    .option("--session <path>", "explicit session jsonl path (overrides auto-detect)")
    .action(async (opts: { tail?: number; graph?: string; session?: string }) => {
      loadEnv();
      const root = projectRoot();
      const graphPath = opts.graph ?? graphPathFor(root);
      const jsonl = opts.session ?? resolveSessionJsonl(root);
      if (!jsonl) {
        console.error("no session jsonl found under ~/.claude/projects/<encoded>/");
        process.exit(1);
        return;
      }

      // Per-project advisory lock. Lock file sits next to the graph.
      const lockTarget = graphPath;
      let release: (() => Promise<void>) | null = null;
      try {
        release = await lockfile.lock(lockTarget, {
          retries: { retries: 3, minTimeout: 100, maxTimeout: 500 },
          realpath: false,
          stale: 60_000,
        });
      } catch (e) {
        console.error(`could not acquire per-project lock on ${lockTarget}: ${(e as Error).message}`);
        process.exit(1);
        return;
      }

      try {
        const caches = cachePaths();
        const embedder = new Embedder({ cachePath: caches.embed });
        const classifier = new TurnClassifier({ cachePath: caches.llm });
        const graph = loadGraph(graphPath);

        console.log(`input:  ${jsonl}`);
        console.log(`output: ${graphPath}`);
        console.log(`initial graph: ${graph.concepts.size} concepts, ${graph.edges.size} edges`);
        console.log();

        const stats = await ingestSession(graph, jsonl, {
          embedder,
          classifier,
          saveEvery: 25,
          saveTo: graphPath,
          tail: opts.tail ?? null,
          onProgress: (n, nc, ne, nl) => {
            console.log(`  [${n}] concepts=${nc} edges=${ne} llm=${nl}`);
          },
        });
        saveGraph(graph, graphPath);

        console.log();
        console.log("=".repeat(60));
        console.log("RESULT");
        console.log("=".repeat(60));
        console.log(`turns:      ${stats.totalTurns}`);
        console.log(`llm calls:  ${stats.llmCalls}`);
        console.log(`cache hits: ${stats.cacheHits}`);
        console.log(`acts:       ${JSON.stringify(stats.actCounts)}`);
        console.log(`concepts:   ${graph.concepts.size}`);
        console.log(`edges:      ${graph.edges.size}`);
        console.log(`elapsed:    ${stats.elapsedS.toFixed(1)}s`);
      } finally {
        if (release) await release();
      }
    });

  const askAction = async (query: string, opts: { class?: string; graph?: string }) => {
    loadEnv();
    const root = projectRoot();
    const graphPath = opts.graph ?? graphPathFor(root);
    const graph = loadGraph(graphPath);
    const caches = cachePaths();
    const embedder = new Embedder({ cachePath: caches.embed });
    const classFilter = opts.class as ConceptClass | undefined;
    const out = await askText(graph, query, { embedder, classFilter });
    console.log(out);
  };

  program
    .command("ask <query>")
    .description("Relevance-first pack for a focus query")
    .option("--class <class>", "restrict to one class")
    .option("--graph <path>", "path to v0.2 graph JSON")
    .action(askAction);

  program
    .command("recall <query>")
    .description("Alias for ask")
    .option("--class <class>", "restrict to one class")
    .option("--graph <path>", "path to v0.2 graph JSON")
    .action(askAction);

  program
    .command("why <topic>")
    .description("Cause-surfaced walk for a topic")
    .option("--graph <path>", "path to v0.2 graph JSON")
    .action(askAction);

  program
    .command("audit")
    .description("Render the 5-signal audit report")
    .option("--graph <path>", "path to v0.2 graph JSON")
    .action((opts: { graph?: string }) => {
      loadEnv();
      const root = projectRoot();
      const graphPath = opts.graph ?? graphPathFor(root);
      const graph = loadGraph(graphPath);
      console.log(formatAudit(audit(graph)));
    });

  program
    .command("replay")
    .description("Chronological turn-by-turn view")
    .option("--session <sid>", "session id to replay")
    .option("--since <n>", "skip turns before this index", (v) => parseInt(v, 10))
    .option("--max-lines <n>", "tail-preserve this many lines", (v) => parseInt(v, 10))
    .option("--graph <path>", "path to v0.2 graph JSON")
    .action((opts: { session?: string; since?: number; maxLines?: number; graph?: string }) => {
      loadEnv();
      const root = projectRoot();
      const graphPath = opts.graph ?? graphPathFor(root);
      const graph = loadGraph(graphPath);
      console.log(replayText(graph, {
        session: opts.session ?? null,
        sinceTurn: opts.since ?? 0,
        maxLines: opts.maxLines ?? 120,
      }));
    });

  return program;
}
