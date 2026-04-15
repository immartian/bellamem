#!/usr/bin/env node
import { buildProgram } from "../src/cli.js";

const program = buildProgram();
program.parseAsync(process.argv).catch((err) => {
  console.error(err);
  process.exit(1);
});
