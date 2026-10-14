import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  buildCliCommands,
  buildCommandPack,
  buildDeployRequestFromJourney,
  buildServerCliCommands,
  buildServerConnectionDraft,
  formatJson,
  JOURNEY_DEPLOY_FIELDS,
  parseSshCommand,
  parseEnvelope,
  parseEventStream,
  summarizeEnvelope,
  validateServerConnectionDraft,
  validateDeployRequest,
} from "./staticAdapter.js";
import { createDefaultJourneyState } from "./storageAdapter.js";

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

function journey(overrides = {}) {
  const base = createDefaultJourneyState();
  return {
    ...base,
    ...overrides,
    deploy: { ...base.deploy, ...overrides.deploy },
    server: { ...base.server, host: "198.51.100.10", ...overrides.server },
  };
}

test("every key the journey builds is a DeployRequest property", () => {
  const known = new Set(Object.keys(deployRequestSchema.properties));
  const byAddress = buildDeployRequestFromJourney(journey(), deployRequestSchema);
  const bySavedServer = buildDeployRequestFromJourney(journey({ selectedServerId: "srv-demo" }), deployRequestSchema);

  for (const key of [...JOURNEY_DEPLOY_FIELDS, ...Object.keys(byAddress), ...Object.keys(bySavedServer)]) {
    assert.ok(known.has(key), `${key} is not in deploy-request.schema.json`);
  }
});

test("a fresh journey builds a request the schema accepts", () => {
  const request = buildDeployRequestFromJourney(journey(), deployRequestSchema);

  assert.deepEqual(validateDeployRequest(request, deployRequestSchema, workflow), []);
});

test("keys the schema does not declare never reach the request", () => {
  const request = buildDeployRequestFromJourney(journey({ deploy: { pq: true } }), deployRequestSchema);

  assert.equal("pq" in request, false);
});

test("validation refuses keys outside a closed schema", () => {
  const request = { ...buildDeployRequestFromJourney(journey(), deployRequestSchema), pq: false };

  const errors = validateDeployRequest(request, deployRequestSchema, workflow);

  assert.deepEqual(errors.map((error) => error.field), ["pq"]);
});

test("validates request fields with readable messages", () => {
  const request = buildDeployRequestFromJourney(
    journey({ deploy: { client_name: "bad name!" }, server: { host: "198.51.100", ssh_user: "bad user" } }),
    deployRequestSchema,
  );

  assert.deepEqual(new Set(validateDeployRequest(request, deployRequestSchema, workflow).map((error) => error.message)), new Set([
    "Enter a valid IP address, or use local when running Meridian on the target server.",
    "Use letters, numbers, hyphens, and underscores.",
    "Use letters, numbers, dots, hyphens, and underscores.",
  ]));
});

test("allows deploy request validation by saved server reference", () => {
  const request = buildDeployRequestFromJourney(journey({ selectedServerId: "srv-demo", server: { host: "" } }), deployRequestSchema);

  const errors = validateDeployRequest(request, deployRequestSchema, workflow);

  assert.equal(errors.some((error) => error.field === "ip"), false);
  assert.equal(errors.some((error) => error.field === "user"), false);
});

test("builds deploy requests from the beginner journey state", () => {
  const request = buildDeployRequestFromJourney({
    confirmDeploy: true,
    deploy: {
      client_name: "default",
      color: "forest",
      domain: "",
      geo_block: true,
      harden: true,
      icon: "",
      server_name: "Family VPN",
      sni: "www.google.com",
      warp: false,
    },
    selectedServerId: "",
    server: {
      host: "198.51.100.10",
      ssh_port: 2222,
      ssh_user: "ubuntu",
      title: "Family VPN",
    },
  });

  assert.deepEqual(request, {
    client_name: "default",
    color: "forest",
    domain: "",
    geo_block: true,
    harden: true,
    icon: "",
    ip: "198.51.100.10",
    server_name: "Family VPN",
    sni: "www.google.com",
    ssh_port: 2222,
    user: "ubuntu",
    warp: false,
    yes: true,
  });
});

test("cleared fields take the workflow defaults only when the request is built", () => {
  const state = journey({ deploy: { client_name: "", server_name: "  ", sni: "" }, server: { ssh_port: "", ssh_user: "" } });

  const request = buildDeployRequestFromJourney(state, deployRequestSchema);

  assert.equal(request.client_name, "default");
  assert.equal(request.server_name, "My VPN");
  assert.equal(request.sni, "www.google.com");
  assert.equal(request.user, "root");
  assert.equal(request.ssh_port, 22);
  assert.equal(state.deploy.client_name, "");
});

test("builds deploy requests by saved server reference and hides raw connection fields", () => {
  const request = buildDeployRequestFromJourney(journey({ selectedServerId: "srv-demo", server: { ssh_port: 2222 } }));

  assert.equal(request.requested_server, "srv-demo");
  assert.equal("ip" in request, false);
  assert.equal("user" in request, false);
  assert.equal("ssh_port" in request, false);
});

test("builds request-file CLI commands", () => {
  assert.deepEqual(buildCliCommands(), {
    deploy: "meridian deploy --request deploy.json --json --events=jsonl",
    dryRun: "meridian deploy --request deploy.json --dry-run --json",
  });
});

test("builds a server connection draft from onboarding state", () => {
  const draft = buildServerConnectionDraft({
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
    host: "198.51.100",
    ssh_port: "70000",
    ssh_user: "bad user",
    title: "",
  });

  assert.deepEqual(
    new Set(validateServerConnectionDraft(draft, serverConnectionDraftSchema, serverWorkflow).map((error) => error.message)),
    new Set([
      "Enter a valid IP address.",
      "SSH port must be 65535 or lower.",
      "Use letters, numbers, dots, hyphens, and underscores.",
    ]),
  );
});

test("auto-generates a server title when beginners leave it blank", () => {
  const draft = buildServerConnectionDraft({
    host: "198.51.100.10",
    ssh_port: "22",
    ssh_user: "root",
    title: "",
  });

  assert.equal(draft.title, "Server 198.51.100.10");
});

test("validation errors include human labels, not only raw field ids", () => {
  const draft = buildServerConnectionDraft({
    host: "198.51.100",
    ssh_port: "70000",
    ssh_user: "bad user",
    title: "",
  });

  const errors = validateServerConnectionDraft(draft, serverConnectionDraftSchema, serverWorkflow);

  assert.equal(errors.some((error) => error.label === "SSH user"), true);
  assert.equal(errors.some((error) => error.label === "ssh_user"), false);
});

test("validates generated schema maxLength constraints", () => {
  const draft = buildServerConnectionDraft({
    host: "198.51.100.10",
    ssh_port: "22",
    ssh_user: "root",
    title: "x".repeat(81),
  });
  const deploy = {
    ...buildDeployRequestFromJourney({
      confirmDeploy: false,
      deploy: {
        client_name: "default",
        color: "ocean",
        domain: "",
        geo_block: true,
        harden: true,
        icon: "",
        server_name: "Family VPN",
        sni: "www.google.com",
        warp: false,
      },
      selectedServerId: "s".repeat(121),
      server: {
        host: "198.51.100.10",
        ssh_port: 22,
        ssh_user: "root",
        title: "Family VPN",
      },
    }),
  };

  assert.match(validateServerConnectionDraft(draft, serverConnectionDraftSchema, serverWorkflow)[0].message, /80/);
  assert.match(validateDeployRequest(deploy, deployRequestSchema, workflow)[0].message, /120/);
});

test("parses pasted SSH commands into server fields", () => {
  assert.deepEqual(parseSshCommand("ssh ubuntu@198.51.100.10 -p 2222"), {
    host: "198.51.100.10",
    ssh_port: 2222,
    ssh_user: "ubuntu",
    title: "Server 198.51.100.10",
  });
  assert.deepEqual(parseSshCommand("ssh -p2222 root@198.51.100.11"), {
    host: "198.51.100.11",
    ssh_port: 2222,
    ssh_user: "root",
    title: "Server 198.51.100.11",
  });
  assert.deepEqual(parseSshCommand("ssh -l admin -o Port=2200 198.51.100.12"), {
    host: "198.51.100.12",
    ssh_port: 2200,
    ssh_user: "admin",
    title: "Server 198.51.100.12",
  });
  assert.deepEqual(parseSshCommand("ssh -i ~/.ssh/id_ed25519 ubuntu@[2001:db8::10]"), {
    host: "2001:db8::10",
    ssh_port: 22,
    ssh_user: "ubuntu",
    title: "Server 2001:db8::10",
  });
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

test("builds a beginner static command pack", () => {
  const pack = buildCommandPack({
    deployRequest: { ip: "198.51.100.10" },
    serverDraft: {
      host: "198.51.100.10",
      ssh_port: 22,
      ssh_user: "root",
      title: "Family VPN",
    },
  });

  assert.equal(pack.testSsh, "ssh root@198.51.100.10");
  assert.equal(pack.setupKey, "ssh-copy-id root@198.51.100.10");
  assert.equal(pack.saveServer, "meridian server add 198.51.100.10 --name family-vpn --user root");
  assert.equal(pack.dryRun, "meridian deploy --request deploy.json --dry-run --json");
});

test("Studio page renders color options from generated workflow", async () => {
  const source = await readText("website/src/pages/studio.astro");

  assert.match(source, /workflowOptions\(deployWorkflow, 'color'\)/);
  assert.doesNotMatch(source, /<option value="lavender">/);
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

test("summarizes failed envelopes with recovery hints", async () => {
  const envelope = parseEnvelope(await readText("contracts/meridian/v1/fixtures/deploy-user-error-envelope.json"));
  const summary = summarizeEnvelope(envelope);

  assert.equal(summary.status, "failed");
  assert.match(summary.text, /Deploy request is incomplete/);
  assert.match(summary.text, /confirmed deploy request/);
});
