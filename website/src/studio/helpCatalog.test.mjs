import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { contractFieldText } from "./contractDefaults.js";
import { deployWorkflow, serverOnboardingWorkflow } from "./generated/index.js";
import { studioHelp, studioJourneySteps } from "./helpCatalog.js";

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

test("Studio help never restates help the workflow contract already gives", () => {
  for (const workflow of [deployWorkflow, serverOnboardingWorkflow]) {
    for (const field of workflow.fields) {
      if (field.help_text) {
        assert.equal(field.id in studioHelp, false, `${field.id} help belongs to the ${workflow.id} contract`);
      }
    }
  }
});

test("contract fields provide label, help, and default placeholder", () => {
  assert.deepEqual(contractFieldText(deployWorkflow, "sni"), {
    label: "Camouflage target",
    help: "A popular website that Reality can impersonate during probes.",
    placeholder: "www.google.com",
  });
  assert.equal(contractFieldText(deployWorkflow, "color", studioHelp).help, studioHelp.color);
  assert.throws(() => contractFieldText(deployWorkflow, "pq"), /no field pq/);
});

test("Studio page is markup only: one module script and no HTML sinks", async () => {
  const page = await readText("website/src/pages/studio.astro");
  const scripts = page.match(/<script[\s\S]*?<\/script>/g) ?? [];

  assert.equal(scripts.length, 1);
  assert.match(scripts[0], /startStudio\(document\)/);
  assert.doesNotMatch(page, /@ts-nocheck/);
  assert.doesNotMatch(page, /<style/);
});

test("Studio's layout runs under the Engine CSP: no inline scripts, no site chrome", async () => {
  const page = await readText("website/src/pages/studio.astro");
  const layout = await readText("website/src/studio/StudioLayout.astro");

  assert.match(page, /<StudioLayout\b/);
  assert.doesNotMatch(page, /layouts\/Base\.astro/);
  for (const script of layout.match(/<script\b[^>]*>/g) ?? []) {
    assert.match(script, /\bsrc=/, `inline script in StudioLayout: ${script}`);
  }
  assert.doesNotMatch(layout, /Nav\.astro|Footer\.astro/);
});
