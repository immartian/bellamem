#!/usr/bin/env node
import { guardMain } from "../src/guard.js";

guardMain().then((code) => process.exit(code)).catch((err) => {
  console.error(err);
  process.exit(1);
});
