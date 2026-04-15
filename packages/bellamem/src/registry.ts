import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { createReadStream } from "node:fs";
import { createInterface } from "node:readline";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { graphPathFor } from "./paths.js";

export interface ProjectEntry {
  /** Stable id = the encoded hyphen-path Claude Code uses. */
  id: string;
  /** Absolute path to the project root on disk. */
  absPath: string;
  /** Absolute path to the project's .graph/v02.json (may not exist yet). */
  graphPath: string;
  /** Where we learned about this project. */
  origin: "claude-code" | "registry";
  /** Most recent jsonl mtime (for sorting home page). */
  lastActivityMs: number;
}

const CLAUDE_PROJECTS_DIR = join(homedir(), ".claude", "projects");

/**
 * Extract the `cwd` field from a Claude Code session jsonl. The first
 * line is often a `file-history-snapshot` with no cwd, so we scan up
 * to `maxLines` records for the first user/assistant record that
 * carries a cwd.
 */
async function extractCwdFromJsonl(path: string, maxLines: number = 40): Promise<string | null> {
  try {
    const rl = createInterface({
      input: createReadStream(path, { encoding: "utf8" }),
      crlfDelay: Infinity,
    });
    let n = 0;
    try {
      for await (const line of rl) {
        if (n++ >= maxLines) break;
        if (!line) continue;
        try {
          const rec = JSON.parse(line) as { cwd?: unknown };
          if (typeof rec.cwd === "string" && rec.cwd.length > 0) {
            return rec.cwd;
          }
        } catch { /* skip */ }
      }
    } finally {
      rl.close();
    }
  } catch { /* unreadable file */ }
  return null;
}

function newestJsonl(dir: string): { path: string; mtimeMs: number } | null {
  try {
    let best: { path: string; mtimeMs: number } | null = null;
    for (const name of readdirSync(dir)) {
      if (!name.endsWith(".jsonl")) continue;
      const p = join(dir, name);
      try {
        const st = statSync(p);
        if (!best || st.mtimeMs > best.mtimeMs) best = { path: p, mtimeMs: st.mtimeMs };
      } catch { /* skip */ }
    }
    return best;
  } catch {
    return null;
  }
}

/**
 * Walk ~/.claude/projects/ and return entries for projects that have
 * a .graph/v02.json at their cwd. Projects without a graph file are
 * omitted (they'd show an empty state that isn't useful yet).
 */
export async function discoverClaudeCodeProjects(): Promise<ProjectEntry[]> {
  if (!existsSync(CLAUDE_PROJECTS_DIR)) return [];
  const out: ProjectEntry[] = [];
  let names: string[] = [];
  try { names = readdirSync(CLAUDE_PROJECTS_DIR); } catch { return out; }

  for (const encoded of names) {
    const dir = join(CLAUDE_PROJECTS_DIR, encoded);
    let st;
    try { st = statSync(dir); } catch { continue; }
    if (!st.isDirectory()) continue;

    const newest = newestJsonl(dir);
    if (!newest) continue;

    const cwd = await extractCwdFromJsonl(newest.path);
    if (!cwd) continue;
    const absPath = resolve(cwd);
    const graphPath = graphPathFor(absPath);
    if (!existsSync(graphPath)) continue;

    out.push({
      id: encoded,
      absPath,
      graphPath,
      origin: "claude-code",
      lastActivityMs: newest.mtimeMs,
    });
  }
  return out;
}

/**
 * ~/.config/bellamem/projects.json — optional manual registry for
 * projects outside the Claude Code projects dir.
 *
 * Shape:
 *   { "roots": ["/abs/path/to/project", ...] }
 */
interface ProjectsJson { roots?: string[] }

function userRegistryPath(): string {
  return join(homedir(), ".config", "bellamem", "projects.json");
}

export function readUserRegistry(): ProjectEntry[] {
  const path = userRegistryPath();
  if (!existsSync(path)) return [];
  try {
    const data = JSON.parse(readFileSync(path, "utf8")) as ProjectsJson;
    const roots = data.roots ?? [];
    const out: ProjectEntry[] = [];
    for (const root of roots) {
      const absPath = resolve(root);
      const graphPath = graphPathFor(absPath);
      if (!existsSync(graphPath)) continue;
      const id = absPath.replace(/\//g, "-").replace(/^-+/, "-");
      let mtime = 0;
      try { mtime = statSync(graphPath).mtimeMs; } catch {}
      out.push({
        id, absPath, graphPath,
        origin: "registry",
        lastActivityMs: mtime,
      });
    }
    return out;
  } catch {
    return [];
  }
}

/**
 * Compose the final project list. Claude Code discoveries first
 * (authoritative source for Claude-Code projects), then user
 * registry entries that aren't already covered.
 */
export async function discoverProjects(): Promise<ProjectEntry[]> {
  const fromCC = await discoverClaudeCodeProjects();
  const seenIds = new Set(fromCC.map(p => p.id));
  const seenPaths = new Set(fromCC.map(p => p.absPath));
  const fromUser = readUserRegistry()
    .filter(p => !seenIds.has(p.id) && !seenPaths.has(p.absPath));
  const all = [...fromCC, ...fromUser];
  all.sort((a, b) => b.lastActivityMs - a.lastActivityMs);
  return all;
}
