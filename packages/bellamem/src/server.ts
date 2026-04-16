import { serve } from "@hono/node-server";
import { Hono } from "hono";
import { SSEStreamingApi, streamSSE } from "hono/streaming";
import { existsSync, statSync, watch, FSWatcher } from "node:fs";
import { readFile } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { loadGraph } from "./store.js";
import { resumeText } from "./resume.js";
import { audit, redFlags } from "./audit.js";
import { askText } from "./walker.js";
import { Embedder } from "./clients.js";
import { ConceptClass } from "./schema.js";
import { listSessions, sessionTrace, massHistogram, replayMassHistory } from "./trace.js";
import { buildHorizon } from "./horizon.js";
import { discoverProjects, ProjectEntry } from "./registry.js";
import { graphPathFor, projectRoot } from "./paths.js";

// ---------------------------------------------------------------------------
// Options + handle
// ---------------------------------------------------------------------------

export interface ServerOptions {
  /** When set, server runs in PINNED mode against this one graph. */
  pinnedGraphPath?: string;
  /** When set, the pinned project's display name. */
  pinnedLabel?: string;
  embedCachePath?: string;
  port?: number;
  webDir?: string;
}

export interface ServerHandle {
  url: string;
  mode: "global" | "pinned";
  projects: ProjectEntry[];
  close: () => Promise<void>;
}

function webDirFromModule(): string {
  const here = dirname(fileURLToPath(import.meta.url));
  const fromSrc = resolve(here, "..", "web");
  if (existsSync(fromSrc)) return fromSrc;
  const fromDist = resolve(here, "..", "..", "web");
  if (existsSync(fromDist)) return fromDist;
  return fromSrc;
}

// ---------------------------------------------------------------------------
// Per-project SSE subscription tracking
// ---------------------------------------------------------------------------

interface Watcher {
  stream: SSEStreamingApi;
  projectId: string | null;   // null = subscribe to everything (home page)
}

// ---------------------------------------------------------------------------
// Build the Hono app
// ---------------------------------------------------------------------------

export async function startServer(opts: ServerOptions = {}): Promise<ServerHandle> {
  const app = new Hono();
  const webDir = opts.webDir ?? webDirFromModule();

  // Build the initial project registry. In pinned mode, the single
  // "project" is a synthetic entry for the given graph.
  const pinned = Boolean(opts.pinnedGraphPath);
  let projects: ProjectEntry[] = [];

  if (pinned) {
    const graphPath = resolve(opts.pinnedGraphPath!);
    const absPath = dirname(dirname(graphPath));  // .../project/.graph/v02.json → .../project
    const id = opts.pinnedLabel ?? "pinned";
    projects = [{
      id,
      absPath,
      graphPath,
      origin: "registry",
      lastActivityMs: existsSync(graphPath) ? statSync(graphPath).mtimeMs : 0,
    }];
  } else {
    projects = await discoverProjects();
  }

  let projectsById = new Map<string, ProjectEntry>();
  for (const p of projects) projectsById.set(p.id, p);

  /** Re-scan projects. Called on /api/projects and when a
   *  /p/:pid route misses the current map. Cheap — reads dirs. */
  const refreshProjects = async () => {
    if (pinned) return; // pinned mode doesn't re-discover
    projects = await discoverProjects();
    projectsById = new Map();
    for (const p of projects) projectsById.set(p.id, p);
  };

  // Request log
  app.use("*", async (c, next) => {
    const start = Date.now();
    await next();
    const ms = Date.now() - start;
    process.stdout.write(`  ${c.req.method} ${c.req.path} ${c.res.status} ${ms}ms\n`);
  });

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------

  const projectFromReq = (pid: string): ProjectEntry | null => {
    return projectsById.get(pid) ?? null;
  };

  // -------------------------------------------------------------------------
  // Home + global endpoints
  // -------------------------------------------------------------------------

  app.get("/api/projects", async (c) => {
    await refreshProjects();
    return c.json(projects.map(p => {
      // Cheap snapshot: load graph, compute the small summary for the
      // home page. For ~4-20 projects this is fast enough; cache if it
      // becomes slow.
      try {
        const g = loadGraph(p.graphPath);
        const r = audit(g);
        const flags = redFlags(r);
        const disputes = [...g.edges.values()].filter(e => e.type === "dispute").length;
        return {
          id: p.id,
          label: basename(p.absPath),
          absPath: p.absPath,
          origin: p.origin,
          lastActivityMs: p.lastActivityMs,
          n_concepts: g.concepts.size,
          n_edges: g.edges.size,
          n_sources: g.sources.size,
          n_sessions: new Set([...g.sources.values()].map(s => s.sessionId)).size,
          n_disputes: disputes,
          n_open_ephemerals: r.ephemeral.open,
          red_flags: flags.map(f => ({ name: f.name, verdict: f.verdict })),
        };
      } catch (err) {
        return {
          id: p.id,
          label: basename(p.absPath),
          absPath: p.absPath,
          origin: p.origin,
          lastActivityMs: p.lastActivityMs,
          error: (err as Error).message,
        };
      }
    }));
  });

  // -------------------------------------------------------------------------
  // Per-project API — /p/:pid/api/...
  // -------------------------------------------------------------------------

  const needProject = async (c: any): Promise<ProjectEntry | null> => {
    const pid = c.req.param("pid");
    let p = projectFromReq(pid);
    if (!p) {
      // Project not in cache — maybe it was created after daemon start.
      await refreshProjects();
      p = projectFromReq(pid);
    }
    return p ?? null;
  };

  app.get("/p/:pid/api/graph", async (c) => {
    const p = await needProject(c);
    if (!p) return c.json({ error: "project not found" }, 404);
    const g = loadGraph(p.graphPath);
    return c.json(g.toJSON());
  });

  app.get("/p/:pid/api/resume", async (c) => {
    const p = await needProject(c);
    if (!p) return c.text("project not found", 404);
    const g = loadGraph(p.graphPath);
    return c.text(resumeText(g));
  });

  app.get("/p/:pid/api/audit", async (c) => {
    const p = await needProject(c);
    if (!p) return c.json({ error: "project not found" }, 404);
    const g = loadGraph(p.graphPath);
    const r = audit(g);
    return c.json({
      ...r,
      histogram: massHistogram(g),
      project: { id: p.id, label: basename(p.absPath), absPath: p.absPath },
    });
  });

  app.get("/p/:pid/api/sessions", async (c) => {
    const p = await needProject(c);
    if (!p) return c.json({ error: "project not found" }, 404);
    const g = loadGraph(p.graphPath);
    return c.json(listSessions(g));
  });

  app.get("/p/:pid/api/session/:sid/trace", async (c) => {
    const p = await needProject(c);
    if (!p) return c.json({ error: "project not found" }, 404);
    const sid = c.req.param("sid");
    const g = loadGraph(p.graphPath);
    const tr = sessionTrace(g, sid);
    if (!tr) return c.json({ error: `session not found: ${sid}` }, 404);
    return c.json(tr);
  });

  app.get("/p/:pid/api/concept/:cid", async (c) => {
    const p = await needProject(c);
    if (!p) return c.json({ error: "project not found" }, 404);
    const cid = c.req.param("cid");
    const g = loadGraph(p.graphPath);
    const concept = g.concepts.get(cid);
    if (!concept) return c.json({ error: `concept not found: ${cid}` }, 404);
    const incoming: Array<{ type: string; source: string; source_topic: string | null }> = [];
    const outgoing: Array<{ type: string; target: string; target_topic: string | null }> = [];
    for (const e of g.edges.values()) {
      if (e.target === cid) {
        incoming.push({
          type: e.type, source: e.source,
          source_topic: g.concepts.get(e.source)?.topic ?? null,
        });
      }
      if (e.source === cid) {
        outgoing.push({
          type: e.type, target: e.target,
          target_topic: g.concepts.get(e.target)?.topic ?? null,
        });
      }
    }
    return c.json({
      concept: concept.toJSON(),
      source_refs_expanded: concept.sourceRefs.map((sr) => {
        const s = g.sources.get(sr);
        return {
          source_id: sr,
          speaker: s?.speaker ?? null,
          text_preview: s?.text ?? null,
          timestamp: s?.timestamp ?? null,
        };
      }),
      mass_history: replayMassHistory(g, concept),
      incoming_edges: incoming,
      outgoing_edges: outgoing,
    });
  });

  app.get("/p/:pid/api/horizon", async (c) => {
    const p = await needProject(c);
    if (!p) return c.json({ error: "project not found" }, 404);
    const minMass = parseFloat(c.req.query("min_mass") ?? "0.55");
    const maxConcepts = parseInt(c.req.query("max_concepts") ?? "80", 10);
    const g = loadGraph(p.graphPath);
    const data = buildHorizon(g, {
      minMass: Number.isFinite(minMass) ? minMass : 0.55,
      maxConcepts: Number.isFinite(maxConcepts) ? maxConcepts : 80,
    });
    return c.json(data);
  });

  app.post("/p/:pid/api/ask", async (c) => {
    const p = await needProject(c);
    if (!p) return c.json({ error: "project not found" }, 404);
    const body = (await c.req.json().catch(() => ({}))) as {
      query?: string; classFilter?: ConceptClass;
    };
    const query = (body.query ?? "").trim();
    if (!query) return c.json({ error: "query required" }, 400);
    const g = loadGraph(p.graphPath);
    const embedder = opts.embedCachePath
      ? new Embedder({ cachePath: opts.embedCachePath })
      : undefined;
    const text = await askText(g, query, { embedder, classFilter: body.classFilter });
    return c.json({ query, text });
  });

  // -------------------------------------------------------------------------
  // SSE watcher — per-project mtime subscriptions
  // -------------------------------------------------------------------------

  const watchers = new Set<Watcher>();

  app.get("/api/watch", (c) =>
    streamSSE(c, async (stream) => {
      const w: Watcher = { stream, projectId: null };
      watchers.add(w);
      await stream.writeSSE({ event: "hello", data: JSON.stringify({ scope: "global" }) });
      await new Promise<void>((done) => stream.onAbort(() => { watchers.delete(w); done(); }));
    }),
  );

  app.get("/p/:pid/api/watch", async (c) => {
    const p = await needProject(c);
    if (!p) return c.json({ error: "project not found" }, 404);
    const pid = p.id;
    return streamSSE(c, async (stream) => {
      const w: Watcher = { stream, projectId: pid };
      watchers.add(w);
      await stream.writeSSE({ event: "hello", data: JSON.stringify({ scope: pid }) });
      await new Promise<void>((done) => stream.onAbort(() => { watchers.delete(w); done(); }));
    });
  });

  // -------------------------------------------------------------------------
  // Static shells
  // -------------------------------------------------------------------------

  const shell = async (name: string): Promise<Response> => {
    const path = join(webDir, `${name}.html`);
    if (!existsSync(path)) {
      return new Response(`missing web shell: ${name}.html`, { status: 500 });
    }
    const html = await readFile(path, "utf8");
    return new Response(html, { headers: { "content-type": "text/html; charset=utf-8" } });
  };

  app.get("/", async () => {
    if (pinned && projects[0]) {
      return new Response(null, { status: 302, headers: { location: `/p/${projects[0]!.id}/overview` } });
    }
    return shell("home");
  });

  app.get("/p/:pid/overview", () => shell("overview"));
  app.get("/p/:pid/graph",    () => shell("graph"));
  app.get("/p/:pid/trace",    () => shell("trace"));
  app.get("/p/:pid/horizon",  () => shell("horizon"));

  app.get("/static/:file", async (c) => {
    const path = join(webDir, "static", c.req.param("file"));
    if (!existsSync(path)) return c.text("not found", 404);
    const body = await readFile(path);
    const file = c.req.param("file");
    const ct = file.endsWith(".css") ? "text/css"
             : file.endsWith(".js")  ? "application/javascript"
             : file.endsWith(".html")? "text/html"
             : "application/octet-stream";
    return new Response(body, { headers: { "content-type": ct } });
  });

  // -------------------------------------------------------------------------
  // Bind port
  // -------------------------------------------------------------------------

  const basePort = opts.port ?? 7878;
  const MAX_PORT_WALK = 22;
  let server: ReturnType<typeof serve> | null = null;
  let boundPort = basePort;
  for (let i = 0; i < MAX_PORT_WALK; i++) {
    const tryPort = basePort + i;
    try {
      server = await new Promise<ReturnType<typeof serve>>((resolvePromise, reject) => {
        const s = serve({ fetch: app.fetch, port: tryPort, hostname: "127.0.0.1" }, () => resolvePromise(s));
        s.on("error", reject);
      });
      boundPort = tryPort;
      break;
    } catch (err) {
      const code = (err as NodeJS.ErrnoException).code;
      if (code === "EADDRINUSE") continue;
      throw err;
    }
  }
  if (!server) throw new Error(`no free port in [${basePort}, ${basePort + MAX_PORT_WALK})`);

  // -------------------------------------------------------------------------
  // File watchers — one per project graph file
  // -------------------------------------------------------------------------

  const fsWatchers: FSWatcher[] = [];
  const lastMtime = new Map<string, number>();
  for (const p of projects) {
    if (!existsSync(p.graphPath)) continue;
    try { lastMtime.set(p.id, statSync(p.graphPath).mtimeMs); } catch {}
    try {
      const w = watch(p.graphPath, { persistent: false }, async () => {
        try {
          const m = statSync(p.graphPath).mtimeMs;
          const prev = lastMtime.get(p.id) ?? 0;
          if (m === prev) return;
          lastMtime.set(p.id, m);
          for (const sub of watchers) {
            if (sub.projectId !== null && sub.projectId !== p.id) continue;
            try {
              await sub.stream.writeSSE({
                event: "reload",
                data: JSON.stringify({ project: p.id, mtime: m }),
              });
            } catch { /* disconnected */ }
          }
        } catch { /* transient */ }
      });
      fsWatchers.push(w);
    } catch { /* some FS types reject fs.watch */ }
  }

  return {
    url: `http://localhost:${boundPort}`,
    mode: pinned ? "pinned" : "global",
    projects,
    close: async () => {
      for (const w of fsWatchers) w.close();
      for (const sub of watchers) {
        try { await sub.stream.close(); } catch {}
      }
      await new Promise<void>((done) => (server as any).close(() => done()));
    },
  };
}

// Back-compat shim for callers that still pass the old shape with
// `graphPath` as a required field. The CLI uses the new shape.
export function legacyGraphPath(): string {
  return graphPathFor(projectRoot());
}
