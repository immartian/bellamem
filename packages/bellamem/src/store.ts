import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync, unlinkSync } from "node:fs";
import { dirname, join } from "node:path";
import { randomBytes } from "node:crypto";
import { Graph, GraphJSON } from "./graph.js";

export function defaultGraphPath(): string {
  return join(process.cwd(), ".graph", "v02.json");
}

/** Atomic write via sibling tempfile + rename. Returns the path written. */
export function saveGraph(graph: Graph, path?: string): string {
  const target = path ?? defaultGraphPath();
  const parent = dirname(target);
  mkdirSync(parent, { recursive: true });
  const data = JSON.stringify(graph.toJSON(), null, 2);
  const tmp = join(parent, `.v02-${randomBytes(6).toString("hex")}.json.tmp`);
  try {
    writeFileSync(tmp, data, "utf8");
    renameSync(tmp, target);
  } catch (err) {
    try { unlinkSync(tmp); } catch {}
    throw err;
  }
  return target;
}

export function loadGraph(path?: string): Graph {
  const target = path ?? defaultGraphPath();
  if (!existsSync(target)) return new Graph();
  const raw = readFileSync(target, "utf8");
  const data = JSON.parse(raw) as GraphJSON;
  return Graph.fromJSON(data);
}
