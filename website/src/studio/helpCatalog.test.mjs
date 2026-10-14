import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { requiredHelpKeys, studioJourneySteps } from "./helpCatalog.js";

const repoRoot = new URL("../../../", import.meta.url);

async function readText(path) {
  return readFile(new URL(path, repoRoot), "utf8");
}

test("Studio journey catalog defines the guided cockpit steps", () => {
  assert.deepEqual(
    studioJourneySteps.map((step) => step.id),
    ["welcome", "server", "connect", "keys", "deploy", "review", "complete"],
  );
  assert.equal(studioJourneySteps.every((step) => step.contextTitle && step.contextBody), true);
});

test("Studio page uses catalog-backed help keys and command sheet", async () => {
  const source = await readText("website/src/pages/studio.astro");
  const helpSource = await readText("website/src/studio/components/StudioHelp.astro");

  for (const key of requiredHelpKeys()) {
    assert.match(source, new RegExp(`helpKey="${key}"`));
  }
  assert.match(source, /<dialog class="studio__sheet" id="command-sheet"/);
  assert.match(source, /id="open-command-sheet"/);
  assert.doesNotMatch(source, /data-fixture=/);
  assert.match(helpSource, /<button class="studio__help-trigger" type="button" aria-label=\{`Help:/);
  assert.match(helpSource, /\.studio__help:focus-within \.studio__help-popover/);
});

test("Studio page keeps the guided cockpit accessible and honest", async () => {
  const source = await readText("website/src/pages/studio.astro");

  assert.doesNotMatch(source, /<section class="studio__task" aria-live=/);
  assert.match(source, /role="status" aria-live="polite"/);
  assert.match(source, /role="alert" aria-live="assertive"/);
  assert.match(source, /button\.setAttribute\('aria-disabled'/);
  assert.match(source, /focusCurrentStep/);
  assert.match(source, /restoreCommandSheetFocus/);
  assert.match(source, /Deploy changes wait for review/);
  assert.match(source, /Key setup may have installed a public SSH key already/);
  assert.match(source, /Run Preview changes so Studio has real plan evidence/);
  assert.match(source, /function deployRequest\(confirm = false\)/);
  assert.doesNotMatch(source, /confirm \|\| journey\.confirmDeploy/);
  assert.match(source, /const request = deployRequest\(\);/);
  assert.match(source, /const request = deployRequest\(true\);/);
});

test("Studio page includes the refreshed visual system primitives", async () => {
  const source = await readText("website/src/pages/studio.astro");

  assert.match(source, /--studio-accent-bg: #7a4d0a/);
  assert.match(source, /color: var\(--studio-accent-ink\)/);
  assert.match(source, /\.studio section\s*\{\s*padding: 0;/);
  assert.match(source, /top: 56px;/);
  assert.match(source, /studio__parse-chips/);
  assert.match(source, /studio__impact-card/);
  assert.match(source, /prefers-reduced-motion: reduce/);
});
