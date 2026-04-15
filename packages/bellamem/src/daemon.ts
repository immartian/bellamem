import { spawn } from "node:child_process";
import {
  existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync,
  openSync, closeSync, statSync,
} from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, join } from "node:path";
import { discoverProjects, ProjectEntry } from "./registry.js";
import { loadGraph } from "./store.js";
import { saveGraph } from "./store.js";
import { Embedder, TurnClassifier } from "./clients.js";
import { ingestSession } from "./ingest.js";
import { startServer, ServerHandle } from "./server.js";

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

function configDir(): string {
  return join(homedir(), ".config", "bellamem");
}
export function daemonPidPath(): string {
  return join(configDir(), "daemon.pid");
}
export function daemonLogPath(): string {
  return join(configDir(), "daemon.log");
}

// ---------------------------------------------------------------------------
// DaemonControl — parent side
// ---------------------------------------------------------------------------

export interface DaemonStartOptions {
  port?: number;
  /** Minutes between save ticks per project. Default 5. */
  saveIntervalMinutes?: number;
}

export interface DaemonStatus {
  running: boolean;
  pid?: number;
  url?: string;
  projects?: number;
  uptime_s?: number;
  last_save?: Record<string, number | null>;
}

function readPid(): number | null {
  const path = daemonPidPath();
  if (!existsSync(path)) return null;
  try {
    const raw = readFileSync(path, "utf8").trim();
    const pid = parseInt(raw, 10);
    if (!Number.isFinite(pid) || pid <= 0) return null;
    return pid;
  } catch { return null; }
}

function processAlive(pid: number): boolean {
  try { process.kill(pid, 0); return true; } catch { return false; }
}

export function daemonIsRunning(): boolean {
  const pid = readPid();
  return pid !== null && processAlive(pid);
}

/**
 * Spawn the daemon detached from the current terminal.
 *
 * Mechanism: re-exec the current Node binary + current script with
 * `BELLAMEM_DAEMON_CHILD=1` in the environment. The child sees the
 * env var and enters `runDaemon()` instead of processing CLI args.
 * Parent writes the PID, then returns immediately.
 */
export async function daemonStart(opts: DaemonStartOptions = {}): Promise<{
  already: boolean; pid: number; url: string;
}> {
  if (daemonIsRunning()) {
    const pid = readPid()!;
    // Best-effort URL readback: the running daemon could be on any
    // port in its walk range. We can't introspect without talking to
    // it, so report the configured default.
    return {
      already: true, pid,
      url: `http://localhost:${opts.port ?? 7878}`,
    };
  }

  mkdirSync(configDir(), { recursive: true });
  // Snapshot current log size so we can read only the child's output.
  const sinceSize = existsSync(daemonLogPath()) ? statSync(daemonLogPath()).size : 0;
  // Open the log file for the child's stdout/stderr.
  const logFd = openSync(daemonLogPath(), "a");

  // Discover the script to re-exec. process.argv[1] is the CLI script
  // that the parent was invoked with; we pass the same to the child.
  const scriptPath = process.argv[1];
  const args = ["--daemon-child"];
  if (opts.port) args.push("--port", String(opts.port));
  if (opts.saveIntervalMinutes) args.push("--save-interval-minutes", String(opts.saveIntervalMinutes));

  const child = spawn(process.execPath, [scriptPath, ...args], {
    detached: true,
    stdio: ["ignore", logFd, logFd],
    env: {
      ...process.env,
      BELLAMEM_DAEMON_CHILD: "1",
    },
  });
  child.unref();
  closeSync(logFd);

  // Write PID. The child will (re)write it with its own announce line.
  writeFileSync(daemonPidPath(), String(child.pid));

  // Wait briefly for the child to write its URL to the log, so we can
  // surface it to the user. Only scan content written after sinceSize
  // to avoid matching a stale "daemon ready" line from a prior run.
  const readyUrl = await waitForDaemonReady(6000, sinceSize);

  return {
    already: false,
    pid: child.pid ?? 0,
    url: readyUrl ?? `http://localhost:${opts.port ?? 7878}`,
  };
}

async function waitForDaemonReady(timeoutMs: number, sinceSize: number): Promise<string | null> {
  const start = Date.now();
  const logPath = daemonLogPath();
  while (Date.now() - start < timeoutMs) {
    if (existsSync(logPath)) {
      try {
        const buf = readFileSync(logPath, "utf8");
        // Only scan content written AFTER the spawn (sinceSize bytes).
        const fresh = buf.slice(sinceSize);
        const m = fresh.match(/daemon ready at (http:\/\/\S+)/);
        if (m) return m[1]!;
      } catch { /* skip */ }
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  return null;
}

export function daemonStop(): { stopped: boolean; pid?: number } {
  const pid = readPid();
  if (pid === null) return { stopped: false };
  if (!processAlive(pid)) {
    try { unlinkSync(daemonPidPath()); } catch {}
    return { stopped: false, pid };
  }
  try {
    process.kill(pid, "SIGTERM");
  } catch {
    return { stopped: false, pid };
  }
  // Wait briefly for exit.
  const deadline = Date.now() + 4000;
  while (Date.now() < deadline) {
    if (!processAlive(pid)) {
      try { unlinkSync(daemonPidPath()); } catch {}
      return { stopped: true, pid };
    }
  }
  // Escalate if still up.
  try { process.kill(pid, "SIGKILL"); } catch {}
  try { unlinkSync(daemonPidPath()); } catch {}
  return { stopped: true, pid };
}

export function daemonStatus(): DaemonStatus {
  const pid = readPid();
  if (pid === null || !processAlive(pid)) return { running: false };
  let uptime: number | undefined;
  try {
    const pidStat = statSync(daemonPidPath());
    uptime = Math.floor((Date.now() - pidStat.mtimeMs) / 1000);
  } catch {}
  return { running: true, pid, uptime_s: uptime };
}

// ---------------------------------------------------------------------------
// DaemonRunner — child side
// ---------------------------------------------------------------------------

interface RunOptions {
  port?: number;
  saveIntervalMinutes?: number;
  /**
   * Per-tick tail window — only scan the last N speaker turns. Keeps
   * each tick fast (<1s) on large sessions (e.g. herenews-app's 242 MB
   * jsonl). 200 turns is ~10 minutes of typical activity, well inside
   * the default 5-minute save interval.
   */
  tailPerTick?: number;
}

/**
 * Child entry point. Called when BELLAMEM_DAEMON_CHILD=1 is set in
 * the environment. Starts the web server, discovers projects, then
 * runs a per-project save loop until SIGTERM.
 */
export async function runDaemon(opts: RunOptions = {}): Promise<void> {
  const saveIntervalMs = (opts.saveIntervalMinutes ?? 5) * 60 * 1000;
  const tailPerTick = opts.tailPerTick ?? 200;

  const embedCachePath = join(
    process.env.TMPDIR ?? "/tmp", "bellamem-proto-tree", "proto-embed-cache.json"
  );
  const llmCachePath = join(
    process.env.TMPDIR ?? "/tmp", "bellamem-proto-tree", "proto-llm-cache.json"
  );

  const handle = await startServer({
    port: opts.port,
    embedCachePath,
  });
  const { url } = handle;
  console.log(`daemon ready at ${url} (${handle.projects.length} projects)`);
  for (const p of handle.projects) console.log(`  · ${p.absPath}`);

  // Per-project save loop — one interval per discovered project.
  // Shared Embedder + Classifier so caches are warm across projects.
  const embedder = new Embedder({ cachePath: embedCachePath });
  const classifier = new TurnClassifier({ cachePath: llmCachePath });
  const savingNow = new Set<string>();   // re-entrancy guard per project

  const saveOnce = async (project: ProjectEntry) => {
    if (savingNow.has(project.id)) return;
    savingNow.add(project.id);
    try {
      // Find the latest jsonl for this project. We use the encoded
      // project id directly — it comes from scanning
      // ~/.claude/projects/* so it matches Claude Code's actual dir
      // name, which is NOT always reversible from the cwd (underscores
      // and slashes both encode to dashes).
      const jsonl = await latestSessionJsonl(project);
      if (!jsonl) {
        console.log(`[${new Date().toISOString()}] ${basename(project.absPath)}: no jsonl, skip`);
        return;
      }
      const graph = loadGraph(project.graphPath);
      const before = { c: graph.concepts.size, e: graph.edges.size };
      const stats = await ingestSession(graph, jsonl, {
        embedder, classifier, saveEvery: 25, saveTo: project.graphPath,
        tail: tailPerTick,
      });
      saveGraph(graph, project.graphPath);
      const after = { c: graph.concepts.size, e: graph.edges.size };
      console.log(
        `[${new Date().toISOString()}] ${basename(project.absPath)}: ` +
        `${stats.totalTurns}t (${stats.llmCalls} new, ${stats.cacheHits} cached), ` +
        `${before.c}→${after.c}c ${before.e}→${after.e}e, ${stats.elapsedS.toFixed(1)}s`
      );
    } catch (err) {
      console.error(`[${new Date().toISOString()}] ${basename(project.absPath)}: ${(err as Error).message}`);
    } finally {
      savingNow.delete(project.id);
    }
  };

  // Kick off a fresh tick for each project with a small stagger so
  // they don't all hit OpenAI at the same millisecond.
  const timers: NodeJS.Timeout[] = [];
  for (let i = 0; i < handle.projects.length; i++) {
    const p = handle.projects[i]!;
    const stagger = i * 500;
    const t = setTimeout(() => {
      const loop = async () => {
        await saveOnce(p);
        timers.push(setTimeout(loop, saveIntervalMs));
      };
      loop();
    }, stagger);
    timers.push(t);
  }

  // SIGTERM handler — clean shutdown.
  const shutdown = async () => {
    console.log(`[${new Date().toISOString()}] daemon shutting down`);
    for (const t of timers) clearTimeout(t);
    try { embedder.save(); classifier.save(); } catch {}
    await handle.close();
    try { unlinkSync(daemonPidPath()); } catch {}
    process.exit(0);
  };
  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);

  // Keep-alive: the interval timers keep the event loop busy, but we
  // also pin the process with a resolved promise that never fulfills.
  await new Promise<void>(() => { /* run until SIGTERM */ });
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function latestSessionJsonl(project: ProjectEntry): Promise<string | null> {
  const { readdirSync, statSync, openSync, readSync, closeSync, existsSync } = await import("node:fs");
  // Claude Code projects live at ~/.claude/projects/<id> where <id>
  // is exactly project.id (captured at discovery time).
  const encoded = join(homedir(), ".claude", "projects", project.id);
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
    try {
      const fd = openSync(c.path, "r");
      const buf = Buffer.alloc(64 * 1024);
      const n = readSync(fd, buf, 0, 64 * 1024, 0);
      closeSync(fd);
      const s = buf.subarray(0, n).toString("utf8");
      if (s.includes('"type":"user"') || s.includes('"type":"assistant"')) {
        return c.path;
      }
    } catch { /* skip */ }
  }
  return null;
}
