#!/usr/bin/env node
import { buildProgram } from "../src/cli.js";
import { runDaemon } from "../src/daemon.js";

// Daemon child handoff: when spawned by `bellamem daemon start`, the
// parent re-execs this script with BELLAMEM_DAEMON_CHILD=1. Intercept
// before CLI parsing so we enter runDaemon() directly.
if (process.env.BELLAMEM_DAEMON_CHILD === "1") {
  const args = process.argv.slice(2);
  const portIdx = args.indexOf("--port");
  const port = portIdx >= 0 ? parseInt(args[portIdx + 1] ?? "", 10) : undefined;
  const siIdx = args.indexOf("--save-interval-minutes");
  const saveIntervalMinutes = siIdx >= 0 ? parseFloat(args[siIdx + 1] ?? "") : undefined;
  runDaemon({
    port: Number.isFinite(port) ? port : undefined,
    saveIntervalMinutes: Number.isFinite(saveIntervalMinutes) ? saveIntervalMinutes : undefined,
  }).catch((err) => {
    console.error("daemon crashed:", err);
    process.exit(1);
  });
} else {
  // Default to `resume` when no subcommand is given, so `/bella`
  // with no args shows the graph resume instead of help text.
  const args = [...process.argv];
  const sub = args[2];
  const knownSubs = [
    "resume", "save", "ask", "recall", "why", "audit", "replay", "evidence",
    "install", "daemon", "serve", "help", "--help", "-h", "-V", "--version",
  ];
  if (!sub || (!knownSubs.includes(sub) && !sub.startsWith("-"))) {
    // No subcommand or unrecognized first arg — inject "resume".
    args.splice(2, 0, "resume");
  }
  const program = buildProgram();
  program.parseAsync(args).catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
