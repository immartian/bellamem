import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";

/**
 * Walk up from `start` looking for a marker directory (.git, package.json,
 * or an existing .graph). Returns the first hit, or `start` itself.
 */
export function projectRoot(start: string = process.cwd()): string {
  let dir = resolve(start);
  while (true) {
    for (const marker of [".git", "package.json", ".graph"]) {
      if (existsSync(join(dir, marker))) return dir;
    }
    const parent = dirname(dir);
    if (parent === dir) return resolve(start);
    dir = parent;
  }
}

export function graphPathFor(root: string): string {
  return join(root, ".graph", "v02.json");
}

/**
 * Claude Code's project-dir encoding: absolute path with slashes
 * replaced by dashes, under `~/.claude/projects/`. Used by the guard
 * hook to locate the session jsonl for the running project.
 */
export function projectDirFor(absPath: string): string {
  const encoded = absPath.replace(/\//g, "-").replace(/^-+/, "-");
  return join(homedir(), ".claude", "projects", encoded);
}
