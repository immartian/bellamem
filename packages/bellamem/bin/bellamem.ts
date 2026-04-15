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
  const program = buildProgram();
  program.parseAsync(process.argv).catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
