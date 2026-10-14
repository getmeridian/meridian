import assert from "node:assert/strict";
import test from "node:test";

import {
  clearJourneyDraft,
  createDefaultJourneyState,
  loadJourneyDraft,
  sanitizeJourneyDraft,
  saveJourneyDraft,
  STUDIO_DRAFT_KEY,
} from "./storageAdapter.js";

class MemoryStorage {
  constructor() {
    this.items = new Map();
  }

  getItem(key) {
    return this.items.get(key) ?? null;
  }

  removeItem(key) {
    this.items.delete(key);
  }

  setItem(key, value) {
    this.items.set(key, value);
  }
}

test("Studio draft persistence keeps progress but excludes secrets", () => {
  const storage = new MemoryStorage();
  const state = {
    ...createDefaultJourneyState(),
    api_token: "api-token",
    currentStep: "keys",
    password: "server-password",
    privateKeyPath: "/Users/example/.ssh/id_ed25519",
    raw_output: "secret output",
    csrf: "csrf-token",
    server: {
      accessToken: "nested-token",
      host: "198.51.100.10",
      password: "nested-password",
      ssh_port: 2222,
      ssh_user: "ubuntu",
      title: "Family VPN",
    },
  };

  assert.equal(saveJourneyDraft(storage, state), true);
  const raw = storage.getItem(STUDIO_DRAFT_KEY);

  assert.match(raw, /Family VPN/);
  assert.doesNotMatch(raw, /server-password/);
  assert.doesNotMatch(raw, /nested-password/);
  assert.doesNotMatch(raw, /csrf-token/);
  assert.doesNotMatch(raw, /api-token/);
  assert.doesNotMatch(raw, /id_ed25519/);
  assert.doesNotMatch(raw, /secret output/);
  assert.doesNotMatch(raw, /nested-token/);
  assert.equal(loadJourneyDraft(storage).currentStep, "keys");
  assert.equal(loadJourneyDraft(storage).readiness.sshValidated, false);
});

test("sanitizeJourneyDraft restores missing defaults", () => {
  const draft = sanitizeJourneyDraft({
    currentStep: "bad-step",
    mode: "bad-mode",
    server: { host: "198.51.100.10" },
    deploy: { warp: true },
  });

  assert.equal(draft.currentStep, "welcome");
  assert.equal("mode" in draft, false);
  assert.equal(draft.server.host, "198.51.100.10");
  assert.equal(draft.server.ssh_user, "root");
  assert.equal(draft.deploy.warp, true);
  assert.equal(draft.deploy.sni, "www.google.com");
  assert.equal("dryRunComplete" in draft.readiness, false);
});

test("sanitizeJourneyDraft keeps non-secret readiness but never a preview without its plan", () => {
  const draft = sanitizeJourneyDraft({
    activeOperationId: "op-resume",
    lastDryRunSignature: '{"ip":"198.51.100.10"}',
    readiness: {
      dryRunComplete: true,
      keyReady: true,
      serverSaved: true,
      sshValidated: true,
    },
  });

  assert.equal(draft.activeOperationId, "op-resume");
  assert.equal("lastDryRunSignature" in draft, false);
  assert.deepEqual(draft.readiness, {
    keyReady: true,
    serverSaved: true,
    sshValidated: true,
  });
});

test("a cleared port stays cleared, and older numeric ports still load", () => {
  assert.equal(sanitizeJourneyDraft({ server: { ssh_port: "" } }).server.ssh_port, "");
  assert.equal(sanitizeJourneyDraft({ server: { ssh_port: 2222 } }).server.ssh_port, "2222");
});

test("clearJourneyDraft removes stored state", () => {
  const storage = new MemoryStorage();
  saveJourneyDraft(storage, createDefaultJourneyState());

  const cleared = clearJourneyDraft(storage);

  assert.equal(storage.getItem(STUDIO_DRAFT_KEY), null);
  assert.equal(cleared.currentStep, "welcome");
});
