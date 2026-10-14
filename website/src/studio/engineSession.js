// @ts-check
// Talks to the localhost Engine that `meridian studio` starts.
//
// The Engine never serves its key. `meridian studio` opens /studio/#k=<key>;
// the page takes the key from the fragment (browsers never send fragments to a
// server), removes it from the address bar and history, and keeps it in this
// tab's sessionStorage. Every API call except the health probe sends it.

import { isTerminalOperation } from "./operationState.js";

/** @typedef {import("./generated/schemas/operation-snapshot.schema").OperationSnapshot} OperationSnapshot */
/** @typedef {import("./generated/schemas/event.schema").Event} EngineEvent */
/** @typedef {"unknown" | "static" | "locked" | "ready"} EngineState */
/**
 * @typedef {object} EngineSessionOptions
 * @property {typeof fetch} [fetchImpl]
 * @property {Pick<Location, "hash" | "pathname" | "search">} [location]
 * @property {Pick<History, "replaceState" | "state">} [history]
 * @property {Pick<Storage, "getItem" | "setItem" | "removeItem"> | null} [storage]
 */

export const ENGINE_KEY_STORAGE_KEY = "meridian.studio.engine-key";
export const HEALTH_PATH = "/api/v1/health";
const KEY_HEADER = "x-meridian-csrf";
// Cheap authenticated read that proves the key without side effects.
const KEY_CHECK_PATH = "/api/v1/operations";

export class EngineRequestError extends Error {
  /**
   * @param {string} message
   * @param {{ category?: string, hint?: string, operation?: OperationSnapshot | null, retryable?: boolean, status?: number }} [options]
   */
  constructor(message, options = {}) {
    super(message);
    this.name = "EngineRequestError";
    this.category = options.category ?? "system";
    this.hint = options.hint ?? "";
    this.operation = options.operation ?? null;
    this.retryable = options.retryable ?? this.category !== "bug";
    this.status = options.status ?? 0;
  }
}

/**
 * Take `#k=` from the address (removing it from the URL and history) or fall back
 * to the key this tab already holds.
 * @param {Required<Pick<EngineSessionOptions, "location" | "history">> & { storage?: EngineSessionOptions["storage"] }} env
 * @returns {string}
 */
export function takeKeyFromLocation({ location, history, storage }) {
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));
  const key = params.get("k") ?? "";
  if (key) {
    storage?.setItem(ENGINE_KEY_STORAGE_KEY, key);
    params.delete("k");
    const rest = params.toString();
    history.replaceState(history.state, "", `${location.pathname}${location.search}${rest ? `#${rest}` : ""}`);
    return key;
  }
  return storage?.getItem(ENGINE_KEY_STORAGE_KEY) ?? "";
}

/**
 * Path of an operation resource; ids always go through encodeURIComponent.
 * @param {string} operationId
 * @param {string} [suffix]
 */
export function operationPath(operationId, suffix = "") {
  return `/api/v1/operations/${encodeURIComponent(operationId)}${suffix ? `/${suffix}` : ""}`;
}

/**
 * @param {EngineSessionOptions} [options]
 */
export function createEngineSession(options = {}) {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch;
  const location = options.location ?? globalThis.location;
  const history = options.history ?? globalThis.history;
  const storage = options.storage === undefined ? safeSessionStorage() : options.storage;
  /** @type {EngineState} */
  let state = "unknown";
  let key = "";
  /** @type {Set<(state: EngineState) => void>} */
  const listeners = new Set();

  /** @param {EngineState} next */
  function setState(next) {
    if (next === state) return;
    state = next;
    for (const listener of listeners) listener(state);
  }

  function forgetKey() {
    if (storage?.getItem(ENGINE_KEY_STORAGE_KEY) === key) {
      storage?.removeItem(ENGINE_KEY_STORAGE_KEY);
    }
    key = "";
  }

  /**
   * @param {string} method
   * @param {string} path
   * @param {unknown} [body]
   */
  async function send(method, path, body) {
    /** @type {Record<string, string>} */
    const headers = { accept: "application/json" };
    if (key) headers[KEY_HEADER] = key;
    if (body !== undefined) headers["content-type"] = "application/json";
    try {
      return await fetchImpl(path, {
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
        headers,
        method,
      });
    } catch {
      throw new EngineRequestError("Could not reach the Studio Engine.", {
        category: "system",
        hint: "Check that `meridian studio` is still running in Terminal.",
      });
    }
  }

  /** Probe for an Engine, then check this tab's key. */
  async function handshake() {
    key = takeKeyFromLocation({ location, history, storage });
    if (!(await probeHealth(fetchImpl))) {
      setState("static");
      return state;
    }
    if (!key) {
      setState("locked");
      return state;
    }
    try {
      const response = await send("GET", KEY_CHECK_PATH);
      if (response.status === 401) {
        forgetKey();
        setState("locked");
      } else {
        setState("ready");
      }
    } catch {
      setState("static");
    }
    return state;
  }

  /**
   * @param {string} method
   * @param {string} path
   * @param {unknown} [body]
   * @returns {Promise<any>}
   */
  async function request(method, path, body) {
    let response = await send(method, path, body);
    if (response.status === 401) {
      // A restarted Engine has a new key. Retry once if the address brought one.
      const previous = key;
      key = takeKeyFromLocation({ location, history, storage });
      if (key && key !== previous) {
        response = await send(method, path, body);
      }
      if (response.status === 401) {
        forgetKey();
        setState("locked");
      }
    }
    const payload = await readJson(response);
    if (!response.ok) {
      throw engineError(payload, response.status);
    }
    if (state !== "ready") setState("ready");
    return payload;
  }

  return {
    handshake,
    get state() {
      return state;
    },
    /** @param {(state: EngineState) => void} listener */
    onChange(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    /** @param {string} path */
    get: (path) => request("GET", path),
    /** @param {string} path @param {unknown} body */
    post: (path, body) => request("POST", path, body),
    /**
     * Ask the Engine to stop an operation at its next safe point; resolves to
     * the typed meridian.operation-cancel/v1 payload ({ outcome, operation }).
     * @param {string} operationId
     */
    cancelOperation: (operationId) => request("POST", operationPath(operationId, "cancel"), {}),
  };
}

/** @typedef {ReturnType<typeof createEngineSession>} EngineSession */

/**
 * @typedef {object} PollHandlers
 * @property {(snapshot: OperationSnapshot) => void} [onSnapshot]
 * @property {(events: EngineEvent[]) => void} [onEvents] only events not delivered before
 * @property {(payload: any) => void} [onFinished] the /result payload, fetched once
 * @property {(info: { failures: number, error: unknown }) => void} [onRetrying]
 * @property {(error: unknown) => void} [onError] polling stopped for good
 * @property {(ms: number) => Promise<void>} [sleep]
 * @property {number} [afterSeq]
 * @property {number} [intervalMs]
 * @property {number} [maxIntervalMs]
 * @property {number} [maxFailures]
 */

/**
 * Follow an operation until it finishes. Reads the snapshot every tick, fetches
 * events only when the snapshot shows new ones, slows down while nothing
 * changes, retries transient failures with backoff, and fetches /result once.
 * @param {Pick<EngineSession, "get">} session
 * @param {string} operationId
 * @param {PollHandlers} [handlers]
 */
export function pollOperation(session, operationId, handlers = {}) {
  const sleep = handlers.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  const intervalMs = handlers.intervalMs ?? 1000;
  const maxIntervalMs = handlers.maxIntervalMs ?? 5000;
  const maxFailures = handlers.maxFailures ?? 20;
  let afterSeq = handlers.afterSeq ?? 0;
  let stopped = false;

  async function loop() {
    let idleTicks = 0;
    let failures = 0;
    while (!stopped) {
      let delay;
      try {
        const status = await session.get(operationPath(operationId));
        if (stopped) return;
        /** @type {OperationSnapshot} */
        const snapshot = status.operation;
        handlers.onSnapshot?.(snapshot);
        if ((snapshot.last_seq ?? 0) > afterSeq) {
          const page = await session.get(operationPath(operationId, `events?after_seq=${afterSeq}`));
          if (stopped) return;
          /** @type {EngineEvent[]} */
          const fresh = (page.events ?? []).filter((/** @type {EngineEvent} */ event) => event.seq > afterSeq);
          afterSeq = Math.max(afterSeq, Number(page.latest_seq ?? 0), ...fresh.map((event) => event.seq));
          if (fresh.length > 0) handlers.onEvents?.(fresh);
          idleTicks = 0;
        } else {
          idleTicks += 1;
        }
        failures = 0;
        if (isTerminalOperation(snapshot)) {
          const result = await session.get(operationPath(operationId, "result"));
          if (!stopped) handlers.onFinished?.(result);
          return;
        }
        delay = Math.min(intervalMs * 1.5 ** idleTicks, maxIntervalMs);
      } catch (error) {
        if (stopped) return;
        failures += 1;
        if (!isTransient(error) || failures >= maxFailures) {
          handlers.onError?.(error);
          return;
        }
        handlers.onRetrying?.({ failures, error });
        delay = Math.min(intervalMs * 2 ** failures, 15000);
      }
      await sleep(delay);
    }
  }

  const done = loop();
  return {
    done,
    stop() {
      stopped = true;
    },
  };
}

/** Network failures and 5xx answers are worth retrying; a lost key or operation is not. */
function isTransient(/** @type {unknown} */ error) {
  if (error instanceof EngineRequestError) {
    return error.status === 0 || error.status >= 500;
  }
  return true;
}

/**
 * @param {typeof fetch} fetchImpl
 * @returns {Promise<boolean>}
 */
async function probeHealth(fetchImpl) {
  if (typeof fetchImpl !== "function") return false;
  try {
    const response = await fetchImpl(HEALTH_PATH, { cache: "no-store", headers: { accept: "application/json" } });
    if (!response.ok) return false;
    const payload = await response.json();
    return payload?.schema === "meridian.engine-health/v1";
  } catch {
    return false;
  }
}

/** @param {Response} response */
async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

/**
 * @param {any} payload
 * @param {number} status
 */
function engineError(payload, status) {
  const detail = payload?.detail;
  if (detail && typeof detail === "object") {
    const message = [detail.message, detail.hint].filter(Boolean).join("\n");
    return new EngineRequestError(message || "Engine request failed.", {
      category: detail.category,
      hint: detail.hint,
      operation: detail.operation,
      retryable: detail.retryable,
      status,
    });
  }
  if (typeof detail === "string" && detail) {
    return new EngineRequestError(detail, { status });
  }
  return new EngineRequestError("Engine request failed.", { status });
}

function safeSessionStorage() {
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
}
