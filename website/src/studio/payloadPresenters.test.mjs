import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  PRESENTED_SCHEMAS,
  presentCancelled,
  presentDeployResult,
  presentFailure,
  presentPayload,
  presentProgress,
  presentReview,
  presentTimelineEvent,
  presentValidationFacts,
} from "./payloadPresenters.js";
import { parseEnvelope } from "./staticAdapter.js";

const repoRoot = new URL("../../../", import.meta.url);

const RESULT = {
  client_name: "default",
  connection_page_path: "/c/abc",
  connection_page_url: "https://198.51.100.10/c/abc",
  domain: "",
  geo_block: true,
  harden: true,
  mode: "first_deploy",
  node_count: 1,
  panel_secret_path: "secret",
  panel_url: "https://198.51.100.10/secret",
  relay_count: 0,
  server_ip: "198.51.100.10",
  sni: "www.google.com",
  ssh_port: 22,
  ssh_user: "root",
  summary: "Deployed 198.51.100.10",
  warp: false,
};

test("each Engine response schema has exactly one presenter", () => {
  assert.deepEqual([...PRESENTED_SCHEMAS].sort(), [
    "meridian.deploy-dry-run/v1",
    "meridian.deploy-operation-result/v1",
    "meridian.output/v1",
    "meridian.server-bootstrap-key/v1",
    "meridian.server-profile/v1",
    "meridian.server-validate/v1",
  ]);
  assert.equal(presentPayload({ schema: "meridian.unknown/v1" }).title, "Action complete");
});

test("SSH validation says what works and what to fix", () => {
  const server = { id: "srv-1", host: "198.51.100.10", title: "Family VPN" };
  assert.deepEqual(
    presentPayload({ schema: "meridian.server-validate/v1", result: { auth_ok: true, sudo_ok: true, reachable: true, detected_os: "Debian 12", server } }),
    { status: "ready", title: "SSH works", detail: "SSH and sudo work on Debian 12.", tone: "ok" },
  );
  const failed = presentPayload({
    schema: "meridian.server-validate/v1",
    result: { auth_ok: false, reachable: true, hints: ["Check the SSH user."], server },
  });
  assert.equal(failed.tone, "warning");
  assert.equal(failed.detail, "Check the SSH user.");
  assert.deepEqual(
    presentValidationFacts({ auth_ok: false, reachable: true, server }).map((fact) => fact.ok),
    [true, false, false, false],
  );
});

test("key setup separates installed from verified", () => {
  const server = { id: "srv-1", host: "198.51.100.10", title: "Family VPN" };
  const verified = presentPayload({ schema: "meridian.server-bootstrap-key/v1", result: { installed: true, verified: true, key_policy: "generate_meridian", server } });
  const unverified = presentPayload({
    schema: "meridian.server-bootstrap-key/v1",
    result: { installed: true, verified: false, key_policy: "generate_meridian", warnings: ["Password login is still on."], server },
  });

  assert.equal(verified.title, "Key login verified");
  assert.equal(unverified.tone, "warning");
  assert.match(unverified.detail, /Password login is still on\.$/);
});

test("pasted envelopes present their first error with its hint", async () => {
  const envelope = parseEnvelope(await readFile(new URL("contracts/meridian/v1/fixtures/deploy-user-error-envelope.json", repoRoot), "utf8"));

  const view = presentPayload(envelope);

  assert.equal(view.status, "failed");
  assert.equal(view.tone, "error");
  assert.match(view.title, /Deploy request is incomplete/);
  assert.match(view.title, /confirmed deploy request/);
});

test("failures branch on category, not on message wording", () => {
  // Same words, different categories: only the category decides the advice.
  const message = "Docker container failed over SSH";
  const user = presentFailure({ message, category: "user" });
  const system = presentFailure({ message, category: "system" });
  const bug = presentFailure({ message, category: "bug" });

  assert.match(user.detail, /Fix what the message names/);
  assert.equal(user.offerDiagnostics, false);
  assert.match(system.detail, /partly prepared/);
  assert.equal(system.retryable, true);
  assert.match(bug.detail, /Meridian bug/);
  assert.equal(bug.retryable, false);
  assert.equal(bug.offerDiagnostics, true);
});

test("a failure's own hint wins over generic advice", () => {
  const view = presentFailure({ message: "SSH login failed\nUse the key from step 4.", category: "user" });

  assert.equal(view.title, "SSH login failed");
  assert.equal(view.detail, "Use the key from step 4.");
});

test("a finished deploy offers the invite, a test, and the next device", () => {
  const view = presentDeployResult({ ...RESULT, domain: "vpn.example.com" });

  assert.equal(view.invite, "https://198.51.100.10/c/abc");
  assert.equal(view.testCommand, "meridian test 198.51.100.10");
  assert.equal(view.addDeviceCommand, "meridian client add phone");
  assert.match(view.steps.at(-1), /Cloudflare for vpn\.example\.com/);
  assert.equal(presentDeployResult(RESULT, { afterCancel: true }).tone, "warning");
});

test("a cancelled deploy says whether the server changed and how to resume", () => {
  const view = presentCancelled({ phase: "apply", message: "Stopped.", remote_changed: true, resume_hint: "Run it again." });

  assert.equal(view.retryLabel, "Resume deploy");
  assert.match(view.serverNote, /already applied/);
  assert.equal(presentCancelled({ phase: "queued", message: "", remote_changed: false }).retryLabel, "Start deploy");
});

test("progress reads the step counter from the latest event", () => {
  const view = presentProgress(
    { data: { step: "Install nginx", index: 3, total: 9 }, message: "Step", phase: "provision", type: "provision.step.started" },
    { state: "running" },
  );

  assert.deepEqual(view, {
    state: "running",
    title: "Deploy in progress",
    body: "Install nginx, step 3 of 9",
    meta: "This can take a few minutes. Keep this tab open.",
  });
  assert.equal(presentTimelineEvent({ seq: 4, type: "warning" }).message, "warning");
});

test("review cards reflect the plan and the request", () => {
  const cards = presentReview({
    targetLabel: "Family VPN (198.51.100.10:22)",
    request: { harden: false, geo_block: true, client_name: "alice", server_name: "My VPN", ip: "198.51.100.10" },
    plan: { mode: "redeploy", server_ip: "198.51.100.10", ports: { reality_port: 1, wss_port: 2, xhttp_port: 3 } },
    planSource: "engine",
  });

  const byTitle = Object.fromEntries(cards.map((card) => [card.title, card]));
  assert.equal(byTitle["VPN services"].badge, "Update");
  assert.equal(byTitle["SSH and firewall"].badge, "Hardening off");
  assert.match(byTitle["First device"].detail, /alice/);
  assert.equal(byTitle["Evidence source"].badge, "Engine");
});
