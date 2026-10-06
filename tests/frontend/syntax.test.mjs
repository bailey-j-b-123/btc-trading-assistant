import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

/**
 * The dashboard has no build step: the browser parses these ES modules directly.
 * A syntax error in any one of them stops `main.js` (and therefore every view)
 * from loading, so every shipped module must actually parse.
 */

const jsRoot = fileURLToPath(
  new URL("../../src/trading_assistant/web/static/js/", import.meta.url),
);

function collectModules(directory) {
  const found = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      found.push(...collectModules(path));
    } else if (entry.name.endsWith(".js")) {
      found.push(path);
    }
  }
  return found;
}

test("every shipped dashboard module parses as an ES module", () => {
  const modules = collectModules(jsRoot);
  assert.ok(modules.length > 5, "expected the dashboard modules to be present");
  const scratch = mkdtempSync(join(tmpdir(), "dashboard-syntax-"));
  try {
    for (const modulePath of modules) {
      const copy = join(scratch, "module.mjs");
      writeFileSync(copy, readFileSync(modulePath));
      try {
        execFileSync(process.execPath, ["--check", copy], { stdio: "pipe" });
      } catch (error) {
        assert.fail(
          `${modulePath.replace(jsRoot, "js/")} is not valid ES module syntax: ` +
            String(error.stderr),
        );
      }
    }
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
});
