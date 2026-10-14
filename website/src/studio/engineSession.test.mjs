import assert from "node:assert/strict";
import test from "node:test";

import {
  createEngineSession,
  ENGINE_KEY_STORAGE_KEY,
  EngineRequestError,
  operationPath,
  pollOperation,
  takeKeyFromLocation,
} from "./engineSession.js";

class MemoryStorage {
  constructor(items = {}) {
    this.items = new Map(Object.entries(items));
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

function fakeBrowser(hash = "") {
  const location = { hash, pathname: "/studio/", search: "" };
  const replaced = [];
  const history = {
    state: null,
    replaceState(_state, _title, url) {
      replaced.push(url);
      const [, fragment = ""] = String(url).split("#");
      location.hash = fragment ? `#${fragment}` : "";
    },
  };
  return { history, location, replaced };
}

function json(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      return payload;
    },
  };
}

const HEALTH = { schema: "meridian.engine-health/v1", status: "ok", assets: { available: true } };

// A fake Engine: health is open, everything else needs `validKey`.
function fakeEngine({ validKey = "key-1", routes = {} } = {}) {
  const calls = [];
  const fetchImpl = async (path, init = {}) => {
    calls.push({ path, init });
    if (path === "/api/v1/health") return json(HEALTH);
    if (init.headers?.["x-meridian-csrf"] !== validKey) {
      return json({ detail: { category: "user", message: "This Studio tab has no valid key.", hint: "Open the address." } }, 401);
    }
    const route = routes[path];
    if (typeof route === "function") return route(init);
    return json(route ?? { schema: "ok" });
  };
  return { calls, fetchImpl };
}

test("the key leaves the address bar and lands in this tab's storage", () => {
  const browser = fakeBrowser("#k=secret-key&step=deploy");
  const storage = new MemoryStorage();

  assert.equal(takeKeyFromLocation({ ...browser, storage }), "secret-key");
  assert.deepEqual(browser.replaced, ["/studio/#step=deploy"]);
  assert.equal(storage.getItem(ENGINE_KEY_STORAGE_KEY), "secret-key");
  assert.equal(takeKeyFromLocation({ ...fakeBrowser(""), storage }), "secret-key");
});

test("a page without an Engine is static", async () => {
  const session = createEngineSession({
    ...fakeBrowser("#k=key-1"),
    fetchImpl: async () => json({ schema: "not-engine" }),
    storage: new MemoryStorage(),
  });

  assert.equal(await session.handshake(), "static");
});

test("an Engine without a key in this tab is locked, and health never needs one", async () => {
  const engine = fakeEngine();
  const session = createEngineSession({ ...fakeBrowser(""), fetchImpl: engine.fetchImpl, storage: new MemoryStorage() });

  assert.equal(await session.handshake(), "locked");
  assert.equal(engine.calls.length, 1);
  assert.equal(engine.calls[0].init.headers["x-meridian-csrf"], undefined);
});

test("a valid key makes the session ready and rides every request", async () => {
  const engine = fakeEngine();
  const session = createEngineSession({ ...fakeBrowser("#k=key-1"), fetchImpl: engine.fetchImpl, storage: new MemoryStorage() });

  assert.equal(await session.handshake(), "ready");
  const payload = await session.post("/api/v1/servers", { title: "Edge" });

  assert.deepEqual(payload, { schema: "ok" });
  const post = engine.calls.at(-1);
  assert.equal(post.init.method, "POST");
  assert.equal(post.init.headers["x-meridian-csrf"], "key-1");
  assert.equal(post.init.body, JSON.stringify({ title: "Edge" }));
});

test("a key from an earlier launch is dropped and the session locks", async () => {
  const engine = fakeEngine({ validKey: "key-2" });
  const storage = new MemoryStorage({ [ENGINE_KEY_STORAGE_KEY]: "key-1" });
  const session = createEngineSession({ ...fakeBrowser(""), fetchImpl: engine.fetchImpl, storage });

  assert.equal(await session.handshake(), "locked");
  assert.equal(storage.getItem(ENGINE_KEY_STORAGE_KEY), null);
});

test("a request rejected for its key retries with the new address key, else locks", async () => {
  const keys = { valid: "key-1" };
  const fetchImpl = async (path, init = {}) => {
    if (path === "/api/v1/health") return json(HEALTH);
    if (init.headers?.["x-meridian-csrf"] !== keys.valid) return json({ detail: { category: "user", message: "No key." } }, 401);
    return json({ schema: "meridian.server-profiles/v1", servers: [] });
  };
  const browser = fakeBrowser("#k=key-1");
  const session = createEngineSession({ ...browser, fetchImpl, storage: new MemoryStorage() });
  await session.handshake();

  keys.valid = "key-2";
  browser.location.hash = "#k=key-2";
  assert.deepEqual(await session.get("/api/v1/servers"), { schema: "meridian.server-profiles/v1", servers: [] });

  keys.valid = "key-3";
  await assert.rejects(
    session.get("/api/v1/servers"),
    (error) => error instanceof EngineRequestError && error.status === 401 && error.category === "user",
  );
  assert.equal(session.state, "locked");
});

test("Engine errors stay typed and keep active operation details", async () => {
  const engine = fakeEngine({
    routes: {
      "/api/v1/deploy/start": () =>
        json(
          {
            detail: {
              category: "user",
              message: "Deploy already running for this target.",
              operation: { id: "op-active", state: "running" },
            },
          },
          409,
        ),
    },
  });
  const session = createEngineSession({ ...fakeBrowser("#k=key-1"), fetchImpl: engine.fetchImpl, storage: new MemoryStorage() });
  await session.handshake();

  await assert.rejects(
    session.post("/api/v1/deploy/start", {}),
    (error) =>
      error instanceof EngineRequestError &&
      error.status === 409 &&
      error.operation?.id === "op-active" &&
      error.retryable === true,
  );
});

test("an unreachable Engine is a typed system error", async () => {
  const session = createEngineSession({
    ...fakeBrowser(""),
    fetchImpl: async () => {
      throw new TypeError("Failed to fetch");
    },
    storage: new MemoryStorage(),
  });

  await assert.rejects(session.get("/api/v1/servers"), (error) => error instanceof EngineRequestError && error.status === 0);
});

test("operation ids are always URL-encoded", async () => {
  assert.equal(operationPath("op/1 x", "cancel"), "/api/v1/operations/op%2F1%20x/cancel");
  const engine = fakeEngine({ routes: { "/api/v1/operations/op%2F1/cancel": () => json({ outcome: "cancel_requested" }) } });
  const session = createEngineSession({ ...fakeBrowser("#k=key-1"), fetchImpl: engine.fetchImpl, storage: new MemoryStorage() });
  await session.handshake();

  assert.equal((await session.cancelOperation("op/1")).outcome, "cancel_requested");
});

function scriptedSession(script) {
  const calls = [];
  return {
    calls,
    async get(path) {
      calls.push(path);
      const next = script.shift();
      if (!next) throw new Error(`unexpected ${path}`);
      assert.equal(path, next.path);
      if (next.error) throw next.error;
      return next.payload;
    },
  };
}

test("polling fetches events only when there are new ones, slows down when idle, and reads the result once", async () => {
  const op = (state, lastSeq) => ({ path: "/api/v1/operations/op-1", payload: { operation: { id: "op-1", state, last_seq: lastSeq } } });
  const events = (after, list, latest) => ({
    path: `/api/v1/operations/op-1/events?after_seq=${after}`,
    payload: { events: list, latest_seq: latest },
  });
  const session = scriptedSession([
    op("running", 2),
    events(0, [{ seq: 1 }, { seq: 2 }], 2),
    op("running", 2),
    op("running", 2),
    op("running", 3),
    events(2, [{ seq: 2 }, { seq: 3 }], 3),
    op("succeeded", 3),
    { path: "/api/v1/operations/op-1/result", payload: { result: { schema: "meridian.deploy-operation-result/v1" } } },
  ]);
  const delivered = [];
  const sleeps = [];
  let finished = null;

  await pollOperation(session, "op-1", {
    onEvents: (list) => delivered.push(list.map((event) => event.seq)),
    onFinished: (payload) => {
      finished = payload;
    },
    sleep: async (ms) => {
      sleeps.push(ms);
    },
  }).done;

  assert.deepEqual(delivered, [[1, 2], [3]]);
  assert.deepEqual(sleeps, [1000, 1500, 2250, 1000]);
  assert.equal(finished.result.schema, "meridian.deploy-operation-result/v1");
  assert.equal(session.calls.filter((path) => path.endsWith("/result")).length, 1);
});

test("polling retries transient failures with backoff and gives up on a lost operation", async () => {
  const unreachable = new EngineRequestError("Could not reach the Studio Engine.", { status: 0 });
  const gone = new EngineRequestError("Operation not found.", { status: 404 });
  const session = scriptedSession([
    { path: "/api/v1/operations/op-1", error: unreachable },
    { path: "/api/v1/operations/op-1", error: unreachable },
    { path: "/api/v1/operations/op-1", error: gone },
  ]);
  const retries = [];
  const sleeps = [];
  let stoppedWith = null;

  await pollOperation(session, "op-1", {
    onRetrying: ({ failures }) => retries.push(failures),
    onError: (error) => {
      stoppedWith = error;
    },
    sleep: async (ms) => {
      sleeps.push(ms);
    },
  }).done;

  assert.deepEqual(retries, [1, 2]);
  assert.deepEqual(sleeps, [2000, 4000]);
  assert.equal(stoppedWith, gone);
});
