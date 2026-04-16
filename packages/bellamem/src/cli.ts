import { Command } from "commander";
import { existsSync, readdirSync, statSync, openSync, readSync, closeSync } from "node:fs";
import { join, resolve } from "node:path";
// `resolve` used below in the serve action
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
import { startServer } from "./server.js";
import { runInstall } from "./install.js";
import { daemonStart, daemonStop, daemonStatus, daemonLogPath, daemonIsRunning } from "./daemon.js";

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

function jsonlHasSpeakerTurn(path: string, maxBytes: number = 64 * 1024): boolean {
  // Cheap prefilter: does the file contain at least one user/assistant
  // record? Fresh metadata-only jsonls (file-history-snapshot etc.)
  // can win the mtime race against an active session and cause the
  // cron to ingest nothing. Read the first chunk and grep.
  try {
    const fd = openSync(path, "r");
    const buf = Buffer.alloc(maxBytes);
    const n = readSync(fd, buf, 0, maxBytes, 0);
    closeSync(fd);
    const s = buf.subarray(0, n).toString("utf8");
    return s.includes('"type":"user"') || s.includes('"type":"assistant"');
  } catch {
    return false;
  }
}

function resolveSessionJsonl(root: string): string | null {
  // Find the most recently modified .jsonl under ~/.claude/projects/<encoded>/
  // that actually contains speaker turns. Skip metadata-only files.
  const encoded = projectDirFor(resolve(root));
  if (!existsSync(encoded)) return null;
  const candidates: Array<{ path: string; mtime: number }> = [];
  for (const name of readdirSync(encoded)) {
    if (!name.endsWith(".jsonl")) continue;
    const p = join(encoded, name);
    try {
      const st = statSync(p);
      candidates.push({ path: p, mtime: st.mtimeMs });
    } catch { /* skip */ }
  }
  candidates.sort((a, b) => b.mtime - a.mtime);
  for (const c of candidates) {
    if (jsonlHasSpeakerTurn(c.path)) return c.path;
  }
  return null;
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
      // Ensure the .graph/ directory exists first — on first-run the
      // graph file (and its parent dir) may not exist yet, and
      // proper-lockfile can't create the .lock file without the dir.
      const { mkdirSync } = await import("node:fs");
      const { dirname } = await import("node:path");
      mkdirSync(dirname(graphPath), { recursive: true });
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

  program
    .command("install")
    .description("Install Claude Code /bellamem slash command + user config template")
    .option("--force", "overwrite existing slash command file")
    .action((opts: { force?: boolean }) => {
      const result = runInstall({ force: opts.force });
      if (result.slashCommandWritten) {
        console.log(`  wrote ${result.slashCommandPath}`);
      } else {
        console.log(`  kept  ${result.slashCommandPath}  (use --force to overwrite)`);
      }
      if (result.envWritten) {
        console.log(`  wrote ${result.envPath}  (add OPENAI_API_KEY to enable ingest)`);
      } else {
        console.log(`  kept  ${result.envPath}`);
      }
      console.log();
      console.log("  Restart Claude Code and try /bellamem in a project.");
    });

  const daemon = program.command("daemon").description("Manage the background daemon (web UI + auto-save)");
  daemon
    .command("start")
    .description("Start the daemon in the background")
    .option("--port <n>", "port to bind (default 7878)", (v) => parseInt(v, 10))
    .option("--save-interval-minutes <n>", "save loop cadence per project (default 5)", (v) => parseFloat(v))
    .action(async (opts: { port?: number; saveIntervalMinutes?: number }) => {
      loadEnv();
      const result = await daemonStart({
        port: opts.port,
        saveIntervalMinutes: opts.saveIntervalMinutes,
      });
      if (result.already) {
        console.log(`daemon already running (pid ${result.pid}) → ${result.url}`);
      } else {
        console.log(`daemon started (pid ${result.pid}) → ${result.url}`);
        console.log(`logs: ${daemonLogPath()}`);
      }
    });
  daemon
    .command("stop")
    .description("Stop the daemon")
    .action(() => {
      const result = daemonStop();
      if (!result.stopped && result.pid === undefined) {
        console.log("daemon not running");
      } else if (!result.stopped) {
        console.log(`daemon pid ${result.pid} was not alive; cleaned pid file`);
      } else {
        console.log(`daemon stopped (was pid ${result.pid})`);
      }
    });
  daemon
    .command("status")
    .description("Report daemon status")
    .action(() => {
      const s = daemonStatus();
      if (!s.running) {
        console.log("not running");
        return;
      }
      console.log(`running · pid ${s.pid}${s.uptime_s !== undefined ? ` · uptime ${s.uptime_s}s` : ""}`);
      console.log(`logs: ${daemonLogPath()}`);
    });
  daemon
    .command("logs")
    .description("Tail the daemon log")
    .option("-f, --follow", "follow new log output")
    .option("-n, --lines <n>", "show last N lines (default 40)", (v) => parseInt(v, 10))
    .action(async (opts: { follow?: boolean; lines?: number }) => {
      const fsMod = await import("node:fs");
      const path = daemonLogPath();
      if (!fsMod.existsSync(path)) {
        console.log(`no log yet at ${path}`);
        return;
      }
      const lines = opts.lines ?? 40;
      const raw = fsMod.readFileSync(path, "utf8").split("\n");
      const tail = raw.slice(-lines).join("\n");
      process.stdout.write(tail);
      if (!tail.endsWith("\n")) process.stdout.write("\n");
      if (opts.follow) {
        let size = fsMod.statSync(path).size;
        const timer = setInterval(() => {
          try {
            const st = fsMod.statSync(path);
            if (st.size > size) {
              const fd = fsMod.openSync(path, "r");
              const buf = Buffer.alloc(st.size - size);
              fsMod.readSync(fd, buf, 0, st.size - size, size);
              fsMod.closeSync(fd);
              process.stdout.write(buf.toString("utf8"));
              size = st.size;
            } else if (st.size < size) {
              size = 0;  // file rotated
            }
          } catch {}
        }, 400);
        process.on("SIGINT", () => { clearInterval(timer); process.exit(0); });
        await new Promise(() => {});
      }
    });

  program
    .command("serve")
    .description("Start the localhost web UI in the foreground (use `daemon start` for background)")
    .option("--port <n>", "port to bind (default 7878)", (v) => parseInt(v, 10))
    .option("--graph <path>", "pin to a single graph instead of discovering projects")
    .option("--no-open", "do not launch the browser on start")
    .action(async (opts: { port?: number; graph?: string; open?: boolean }) => {
      loadEnv();
      const caches = cachePaths();
      const handle = await startServer({
        pinnedGraphPath: opts.graph ? resolve(opts.graph) : undefined,
        embedCachePath: caches.embed,
        port: opts.port,
      });
      if (handle.mode === "pinned") {
        console.log(`bellamem web ui → ${handle.url}  (pinned: ${opts.graph})`);
      } else {
        console.log(`bellamem web ui → ${handle.url}  (${handle.projects.length} projects discovered)`);
        for (const p of handle.projects.slice(0, 10)) {
          console.log(`  · ${p.absPath}`);
        }
        if (handle.projects.length > 10) {
          console.log(`  · … +${handle.projects.length - 10} more`);
        }
      }
      if (opts.open !== false) {
        try {
          const { default: open } = await import("open");
          await open(handle.url);
        } catch { /* ignore */ }
      }
      const stop = async () => {
        console.log("\nshutting down…");
        await handle.close();
        process.exit(0);
      };
      process.on("SIGINT", stop);
      process.on("SIGTERM", stop);
    });

  return program;
}
