import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  buildCliCommands,
  buildDeployRequest,
  buildServerCliCommands,
  buildServerConnectionDraft,
  formatJson,
  initialFormState,
  parseEnvelope,
  parseEventStream,
  validateServerConnectionDraft,
  validateDeployRequest,
} from "./staticAdapter.js";

const repoRoot = new URL("../../../", import.meta.url);

async function readJson(path) {
  return JSON.parse(await readFile(new URL(path, repoRoot), "utf8"));
}

async function readText(path) {
  return readFile(new URL(path, repoRoot), "utf8");
}

const workflow = await readJson("contracts/meridian/v1/workflows/deploy.json");
const serverWorkflow = await readJson("contracts/meridian/v1/workflows/server-onboarding.json");
const deployRequestSchema = await readJson("contracts/meridian/v1/schemas/deploy-request.schema.json");
const serverConnectionDraftSchema = await readJson("contracts/meridian/v1/schemas/server-connection-draft.schema.json");

test("builds a DeployRequest from generated workflow form state", () => {
  const state = {
    ...initialFormState(workflow),
    client_name: "alice",
    confirm: true,
    ip: "198.51.100.10",
    server_name: "Family VPN",
    user: "admin",
  };

  assert.deepEqual(buildDeployRequest(state), {
    client_name: "alice",
    color: "ocean",
    domain: "",
    geo_block: true,
    harden: true,
    icon: "",
    ip: "198.51.100.10",
    pq: false,
    server_name: "Family VPN",
    sni: "www.microsoft.com",
    user: "admin",
    warp: false,
    yes: true,
  });
});

test("validates request fields with readable messages", () => {
  const request = buildDeployRequest({
    ...initialFormState(workflow),
    client_name: "bad name!",
    ip: "198.51.100",
    user: "bad user",
  });

  assert.deepEqual(new Set(validateDeployRequest(request, deployRequestSchema, workflow).map((error) => error.message)), new Set([
    "Enter a valid IP address, or use local when running Meridian on the target server.",
    "Use letters, numbers, hyphens, and underscores.",
    "Use letters, numbers, dots, hyphens, and underscores.",
  ]));
});

test("builds request-file CLI commands", () => {
  assert.deepEqual(buildCliCommands(), {
    deploy: "meridian deploy --request deploy.json --json --events=jsonl",
    dryRun: "meridian deploy --request deploy.json --dry-run --json",
  });
});

test("builds a server connection draft from onboarding state", () => {
  const draft = buildServerConnectionDraft({
    ...initialFormState(serverWorkflow),
    host: "198.51.100.10",
    ssh_port: "2222",
    ssh_user: "ubuntu",
    title: "Family VPN",
  });

  assert.deepEqual(draft, {
    host: "198.51.100.10",
    ssh_port: 2222,
    ssh_user: "ubuntu",
    title: "Family VPN",
  });
});

test("validates server onboarding fields with readable messages", () => {
  const draft = buildServerConnectionDraft({
    ...initialFormState(serverWorkflow),
    host: "198.51.100",
    ssh_port: "70000",
    ssh_user: "bad user",
    title: "",
  });

  assert.deepEqual(
    new Set(validateServerConnectionDraft(draft, serverConnectionDraftSchema, serverWorkflow).map((error) => error.message)),
    new Set([
      "Enter a valid IP address.",
      "Server title is required.",
      "SSH port must be 65535 or lower.",
      "Use letters, numbers, dots, hyphens, and underscores.",
    ]),
  );
});

test("builds static server setup commands without storing secrets", () => {
  assert.deepEqual(
    buildServerCliCommands({
      host: "198.51.100.10",
      ssh_port: 2222,
      ssh_user: "ubuntu",
      title: "Family VPN",
    }),
    {
      connect: "ssh -p 2222 ubuntu@198.51.100.10",
      copyKey: "ssh-copy-id -p 2222 ubuntu@198.51.100.10",
      save: "meridian server add 198.51.100.10 --name family-vpn --user ubuntu --ssh-port 2222",
    },
  );
});

test("Studio page avoids raw HTML sinks for pasted output", async () => {
  const source = await readText("website/src/pages/studio.astro");

  assert.doesNotMatch(source, /\.innerHTML\s*=/);
});

test("parses fixture envelopes and event streams", async () => {
  const envelope = parseEnvelope(await readText("contracts/meridian/v1/fixtures/deploy-dry-run-envelope.json"));
  const events = parseEventStream(await readText("contracts/meridian/v1/fixtures/deploy-dry-run-events.jsonl"));

  assert.equal(envelope.schema, "meridian.output/v1");
  assert.equal(envelope.data.server_ip, "198.51.100.10");
  assert.deepEqual(
    events.map((event) => event.type),
    ["command.started", "command.completed"],
  );
  assert.match(formatJson(envelope), /Deploy plan: first_deploy/);
});
