import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import envPaths from "env-paths";

/** Parse a dotenv-style file and return its key→value map (no OS write). */
function parseEnvFile(path: string): Record<string, string> {
  if (!existsSync(path)) return {};
  const out: Record<string, string> = {};
  const raw = readFileSync(path, "utf8");
  for (const line of raw.split("\n")) {
    const s = line.trim();
    if (!s || s.startsWith("#") || !s.includes("=")) continue;
    const eq = s.indexOf("=");
    const k = s.slice(0, eq).trim();
    let v = s.slice(eq + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    out[k] = v;
  }
  return out;
}

export function userConfigEnvPath(): string {
  return join(envPaths("bellamem", { suffix: "" }).config, ".env");
}

/**
 * Load env with precedence: shell > cwd .env > user config .env.
 * Only sets keys that aren't already in process.env.
 */
export function loadEnv(): void {
  const layers = [
    join(process.cwd(), ".env"),
    userConfigEnvPath(),
  ];
  for (const p of layers) {
    const parsed = parseEnvFile(p);
    for (const [k, v] of Object.entries(parsed)) {
      if (process.env[k] === undefined) process.env[k] = v;
    }
  }
}
