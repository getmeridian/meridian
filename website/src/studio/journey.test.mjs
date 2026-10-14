import assert from "node:assert/strict";
import test from "node:test";

import {
  canStartDeploy,
  editDeploy,
  editServer,
  isPreviewCurrent,
  recordPayload,
  requestSignature,
  selectSavedServer,
  stepGate,
  stepProgress,
} from "./journey.js";
import { buildDeployRequestFromJourney } from "./staticAdapter.js";
import { createDefaultJourneyState } from "./storageAdapter.js";

const PLAN = {
  info_page_path: "",
  mode: "first_deploy",
  node_count: 1,
  ports: { reality_port: 10746, wss_port: 0, xhttp_port: 39529 },
  relay_count: 0,
  secret_path: "[redacted]",
  server_ip: "198.51.100.10",
  ws_path: "",
  xhttp_path: "0123456789abcdef",
};

function context(overrides = {}) {
  const journey = { ...createDefaultJourneyState(), ...overrides.journey };
  return {
    journey,
    serverValid: true,
    deployValid: true,
    preview: null,
    requestSignature: requestSignature(buildDeployRequestFromJourney(journey)),
    hasOperation: false,
    ...overrides,
    journey,
  };
}

test("request signatures ignore key order", () => {
  assert.equal(requestSignature({ a: 1, b: 2 }), requestSignature({ b: 2, a: 1 }));
  assert.notEqual(requestSignature({ a: 1 }), requestSignature({ a: 2 }));
});

test("review needs valid options and a preview of exactly the current request", () => {
  const ctx = context();
  const preview = { plan: PLAN, source: "engine", signature: ctx.requestSignature };

  assert.equal(stepGate("review", { ...ctx, deployValid: false, preview }).allowed, false);
  assert.match(stepGate("review", ctx).reason, /Run Preview changes/);
  assert.match(stepGate("review", { ...ctx, preview: { ...preview, signature: "stale" } }).reason, /changed/);
  assert.equal(stepGate("review", { ...ctx, preview }).allowed, true);
});

test("later steps open as the journey earns them", () => {
  const ctx = context();

  assert.equal(stepGate("server", ctx).allowed, true);
  assert.equal(stepGate("connect", { ...ctx, serverValid: false }).allowed, false);
  assert.equal(stepGate("connect", ctx).allowed, true);
  assert.equal(stepGate("deploy", ctx).allowed, false);
  assert.equal(
    stepGate("deploy", context({ journey: { readiness: { keyReady: false, serverSaved: true, sshValidated: true } } })).allowed,
    true,
  );
  assert.equal(stepGate("complete", ctx).allowed, false);
  assert.equal(stepGate("complete", { ...ctx, hasOperation: true }).allowed, true);
  assert.equal(stepGate("nowhere", ctx).allowed, false);
});

test("steps already passed stay open", () => {
  const ctx = context({ journey: { currentStep: "keys" } });

  assert.equal(stepGate("connect", { ...ctx, serverValid: false }).allowed, true);
});

test("rail states follow readiness and the current preview", () => {
  const ctx = context({ journey: { currentStep: "deploy", readiness: { keyReady: true, serverSaved: true, sshValidated: true } } });

  assert.equal(stepProgress("deploy", ctx), "active");
  assert.equal(stepProgress("keys", ctx), "done");
  assert.equal(stepProgress("review", ctx), "locked");
  assert.equal(
    stepProgress("review", { ...ctx, preview: { plan: PLAN, source: "engine", signature: ctx.requestSignature } }),
    "available",
  );
});

test("changing how Meridian reaches the server drops its proven readiness", () => {
  const ready = {
    ...createDefaultJourneyState(),
    selectedServerId: "srv-1",
    readiness: { keyReady: true, serverSaved: true, sshValidated: true },
  };

  const renamed = editServer(ready, { title: "Family VPN" });
  const moved = editServer(ready, { host: "198.51.100.20" });

  assert.equal(renamed.readiness.sshValidated, true);
  assert.equal(renamed.selectedServerId, "srv-1");
  assert.deepEqual(moved.readiness, { keyReady: false, serverSaved: false, sshValidated: false });
  assert.equal(moved.selectedServerId, "");
  assert.equal(ready.server.host, "", "edits never mutate the previous state");
});

test("a deploy edit invalidates a preview through its signature", () => {
  const journey = createDefaultJourneyState();
  const signature = requestSignature(buildDeployRequestFromJourney(journey));
  const preview = { plan: PLAN, source: "engine", signature };

  const edited = editDeploy(journey, { sni: "www.example.com" });

  assert.equal(isPreviewCurrent(preview, signature), true);
  assert.equal(isPreviewCurrent(preview, requestSignature(buildDeployRequestFromJourney(edited))), false);
});

test("saved servers bring the readiness the Engine recorded", () => {
  const journey = selectSavedServer(createDefaultJourneyState(), {
    auth_state: "key_ready",
    host: "198.51.100.10",
    id: "srv-1",
    ssh_port: 2222,
    ssh_user: "ubuntu",
    title: "Family VPN",
  });

  assert.equal(journey.selectedServerId, "srv-1");
  assert.deepEqual(journey.readiness, { keyReady: true, serverSaved: true, sshValidated: true });
  assert.equal(journey.server.ssh_port, "2222");
});

test("Engine answers fold into readiness", () => {
  let journey = createDefaultJourneyState();
  journey = recordPayload(journey, { schema: "meridian.server-profile/v1", server: { id: "srv-1" } });
  assert.equal(journey.readiness.serverSaved, true);
  assert.equal(journey.selectedServerId, "srv-1");

  journey = recordPayload(journey, {
    schema: "meridian.server-validate/v1",
    result: { auth_ok: true, sudo_ok: false, server: { id: "srv-1" } },
  });
  assert.equal(journey.readiness.sshValidated, false);

  journey = recordPayload(journey, { schema: "meridian.server-bootstrap-key/v1", result: { verified: true, server: { id: "srv-1" } } });
  assert.deepEqual(journey.readiness, { keyReady: true, serverSaved: true, sshValidated: true });

  assert.equal(recordPayload(journey, { schema: "meridian.deploy-dry-run/v1" }), journey);
});

test("Start deploy needs the Engine, valid options, confirmation, and a current preview", () => {
  const signature = "sig";
  const preview = { plan: PLAN, source: "engine", signature };
  const ready = { engineReady: true, deployValid: true, confirmed: true, preview, requestSignature: signature };

  assert.equal(canStartDeploy(ready), true);
  for (const missing of [
    { engineReady: false },
    { deployValid: false },
    { confirmed: false },
    { preview: null },
    { requestSignature: "other" },
  ]) {
    assert.equal(canStartDeploy({ ...ready, ...missing }), false, JSON.stringify(missing));
  }
});
