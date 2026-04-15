import { serve } from "@hono/node-server";
import { serveStatic } from "@hono/node-server/serve-static";
import { Hono } from "hono";
import { SSEStreamingApi, streamSSE } from "hono/streaming";
import { existsSync, statSync, watch, FSWatcher } from "node:fs";
import { readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { loadGraph } from "./store.js";
import { resumeText } from "./resume.js";
import { audit } from "./audit.js";
import { askText } from "./walker.js";
import { Embedder } from "./clients.js";
import { ConceptClass, Concept, Edge } from "./schema.js";
import { listSessions, sessionTrace, massHistogram, replayMassHistory } from "./trace.js";

export interface ServerOptions {
  graphPath: string;
  embedCachePath?: string;
  port?: number;
  webDir?: string;
}

interface ServerHandle {
  url: string;
  close: () => Promise<void>;
}

function webDirFromModule(): string {
  // In source: packages/bellamem/src/server.ts  → ../web
  // In dist:   packages/bellamem/dist/src/server.js → ../../web
  const here = dirname(fileURLToPath(import.meta.url));
  const fromSrc = resolve(here, "..", "web");
  if (existsSync(fromSrc)) return fromSrc;
  const fromDist = resolve(here, "..", "..", "web");
  if (existsSync(fromDist)) return fromDist;
  return fromSrc;  // fallback; error surfaces on first static request
}

export function buildApp(opts: ServerOptions): {
  app: Hono;
  watchers: Set<SSEStreamingApi>;
  fsWatcher: FSWatcher | null;
} {
  const app = new Hono();
  const watchers = new Set<SSEStreamingApi>();
  const webDir = opts.webDir ?? webDirFromModule();

  // Request log — one line per request, no framework noise.
  app.use("*", async (c, next) => {
    const start = Date.now();
    await next();
    const ms = Date.now() - start;
    process.stdout.write(`  ${c.req.method} ${c.req.path} ${c.res.status} ${ms}ms\n`);
  });

  // -------------------------------------------------------------------------
  // API endpoints
  // -------------------------------------------------------------------------

  app.get("/api/graph", (c) => {
    const g = loadGraph(opts.graphPath);
    return c.json(g.toJSON());
  });

  app.get("/api/resume", (c) => {
    const g = loadGraph(opts.graphPath);
    return c.text(resumeText(g));
  });

  app.get("/api/audit", (c) => {
    const g = loadGraph(opts.graphPath);
    const r = audit(g);
    return c.json({
      ...r,
      histogram: massHistogram(g),
    });
  });

  app.get("/api/sessions", (c) => {
    const g = loadGraph(opts.graphPath);
    return c.json(listSessions(g));
  });

  app.get("/api/session/:sid/trace", (c) => {
    const sid = c.req.param("sid");
    const g = loadGraph(opts.graphPath);
    const tr = sessionTrace(g, sid);
    if (!tr) return c.json({ error: `session not found: ${sid}` }, 404);
    return c.json(tr);
  });

  app.get("/api/concept/:cid", (c) => {
    const cid = c.req.param("cid");
    const g = loadGraph(opts.graphPath);
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

  app.post("/api/ask", async (c) => {
    const body = (await c.req.json().catch(() => ({}))) as {
      query?: string;
      classFilter?: ConceptClass;
    };
    const query = (body.query ?? "").trim();
    if (!query) return c.json({ error: "query required" }, 400);
    const g = loadGraph(opts.graphPath);
    const embedder = opts.embedCachePath
      ? new Embedder({ cachePath: opts.embedCachePath })
      : undefined;
    const text = await askText(g, query, {
      embedder,
      classFilter: body.classFilter,
    });
    return c.json({ query, text });
  });

  // -------------------------------------------------------------------------
  // SSE watcher — broadcast reload events on graph file mtime change
  // -------------------------------------------------------------------------

  app.get("/api/watch", (c) => {
    return streamSSE(c, async (stream) => {
      watchers.add(stream);
      await stream.writeSSE({ event: "hello", data: JSON.stringify({ now: Date.now() }) });
      // Keep the stream open until the client disconnects.
      await new Promise<void>((resolvePromise) => {
        stream.onAbort(() => {
          watchers.delete(stream);
          resolvePromise();
        });
      });
    });
  });

  // -------------------------------------------------------------------------
  // Static HTML + JS + CSS
  // -------------------------------------------------------------------------

  app.get("/", (c) => c.redirect("/overview"));

  const shell = async (name: string): Promise<Response> => {
    const path = join(webDir, `${name}.html`);
    if (!existsSync(path)) {
      return new Response(`missing web shell: ${name}.html`, { status: 500 });
    }
    const html = await readFile(path, "utf8");
    return new Response(html, { headers: { "content-type": "text/html; charset=utf-8" } });
  };

  app.get("/overview", () => shell("overview"));
  app.get("/graph", () => shell("graph"));
  app.get("/trace", () => shell("trace"));

  app.use("/static/*", serveStatic({ root: webDir.replace(process.cwd() + "/", "") }));
  // Some environments need an absolute path for serveStatic; fall back:
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

  return { app, watchers, fsWatcher: null };
}

export async function startServer(opts: ServerOptions): Promise<ServerHandle> {
  const { app, watchers } = buildApp(opts);

  const basePort = opts.port ?? 7878;
  let port = basePort;
  const MAX_PORT_WALK = 22;

  let server: ReturnType<typeof serve> | null = null;
  for (let i = 0; i < MAX_PORT_WALK; i++) {
    try {
      server = await new Promise<ReturnType<typeof serve>>((resolvePromise, reject) => {
        const s = serve({ fetch: app.fetch, port, hostname: "127.0.0.1" }, () => resolvePromise(s));
        s.on("error", reject);
      });
      break;
    } catch (err) {
      const code = (err as NodeJS.ErrnoException).code;
      if (code === "EADDRINUSE") { port++; continue; }
      throw err;
    }
  }
  if (!server) {
    throw new Error(`no free port in [${basePort}, ${basePort + MAX_PORT_WALK})`);
  }

  // File watcher — broadcast reload to SSE clients on mtime change
  let lastMtime = 0;
  if (existsSync(opts.graphPath)) {
    try { lastMtime = statSync(opts.graphPath).mtimeMs; } catch {}
  }
  const fsWatcher = existsSync(opts.graphPath)
    ? watch(opts.graphPath, { persistent: false }, async () => {
        try {
          const m = statSync(opts.graphPath).mtimeMs;
          if (m === lastMtime) return;
          lastMtime = m;
          for (const s of watchers) {
            try {
              await s.writeSSE({ event: "reload", data: JSON.stringify({ mtime: m }) });
            } catch { /* client disconnected */ }
          }
        } catch { /* file temporarily missing during atomic rename */ }
      })
    : null;

  const url = `http://localhost:${port}`;
  return {
    url,
    close: async () => {
      fsWatcher?.close();
      for (const s of watchers) {
        try { await s.close(); } catch {}
      }
      await new Promise<void>((resolvePromise) => {
        (server as any).close(() => resolvePromise());
      });
    },
  };
}
