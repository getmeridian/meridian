import assert from "node:assert/strict";
import test from "node:test";

import { connectLocalEngine, enginePost, EngineRequestError } from "./localEngineAdapter.js";

function jsonResponse(payload, ok = true) {
  return {
    ok,
    async json() {
      return payload;
    },
  };
}

test("connectLocalEngine detects a local Engine health response", async () => {
  const calls = [];
  const engine = await connectLocalEngine(async (path, init) => {
    calls.push([path, init]);
    return jsonResponse({
      assets: { available: true },
      csrf_token: "csrf",
      schema: "meridian.engine-health/v1",
      status: "ok",
    });
  });

  assert.equal(engine.csrfToken, "csrf");
  assert.equal(engine.assets.available, true);
  assert.equal(calls[0][0], "/api/v1/health");
});

test("connectLocalEngine stays null for static hosting", async () => {
  const engine = await connectLocalEngine(async () => jsonResponse({ schema: "not-engine" }));

  assert.equal(engine, null);
});

test("enginePost sends JSON with the Engine CSRF token", async () => {
  const calls = [];
  const payload = await enginePost({ csrfToken: "csrf" }, "/api/v1/servers", { title: "Edge" }, async (path, init) => {
    calls.push([path, init]);
    return jsonResponse({ schema: "ok" });
  });

  assert.deepEqual(payload, { schema: "ok" });
  assert.equal(calls[0][0], "/api/v1/servers");
  assert.equal(calls[0][1].headers["x-meridian-csrf"], "csrf");
  assert.equal(calls[0][1].body, JSON.stringify({ title: "Edge" }));
});

test("enginePost raises readable typed Engine errors", async () => {
  await assert.rejects(
    enginePost({ csrfToken: "csrf" }, "/api/v1/servers", {}, async () =>
      jsonResponse(
        { detail: { category: "user", message: "Invalid request body", hint: "- host: Enter a valid IP address." } },
        false,
      ),
    ),
    (error) =>
      error instanceof EngineRequestError &&
      error.category === "user" &&
      /host: Enter a valid IP address/.test(error.message),
  );
});

test("enginePost preserves active operation details from Engine errors", async () => {
  await assert.rejects(
    enginePost({ csrfToken: "csrf" }, "/api/v1/deploy/start", {}, async () =>
      jsonResponse(
        {
          detail: {
            category: "user",
            message: "Deploy already running for this target.",
            operation: { id: "op-active", state: "running" },
          },
        },
        false,
      ),
    ),
    (error) =>
      error instanceof EngineRequestError &&
      error.operation?.id === "op-active" &&
      error.retryable === true,
  );
});
