export class EngineRequestError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = "EngineRequestError";
    this.category = options.category ?? "system";
    this.hint = options.hint ?? "";
    this.operation = options.operation ?? null;
    this.retryable = options.retryable ?? this.category !== "bug";
  }
}

export async function connectLocalEngine(fetchImpl = globalThis.fetch) {
  if (typeof fetchImpl !== "function") {
    return null;
  }
  try {
    const response = await fetchImpl("/api/v1/health", {
      cache: "no-store",
      headers: { accept: "application/json" },
    });
    if (!response.ok) {
      return null;
    }
    const payload = await response.json();
    if (payload?.schema !== "meridian.engine-health/v1" || !payload.csrf_token) {
      return null;
    }
    return {
      assets: payload.assets ?? {},
      csrfToken: payload.csrf_token,
      status: payload.status ?? "ok",
    };
  } catch {
    return null;
  }
}

export async function enginePost(engine, path, body, fetchImpl = globalThis.fetch) {
  const response = await fetchImpl(path, {
    body: JSON.stringify(body),
    cache: "no-store",
    headers: {
      accept: "application/json",
      "content-type": "application/json",
      "x-meridian-csrf": engine.csrfToken,
    },
    method: "POST",
  });
  const payload = await readJson(response);
  if (!response.ok) {
    throw engineError(payload);
  }
  return payload;
}

export async function engineGet(path, fetchImpl = globalThis.fetch) {
  const response = await fetchImpl(path, {
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  const payload = await readJson(response);
  if (!response.ok) {
    throw engineError(payload);
  }
  return payload;
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

function engineError(payload) {
  const detail = payload?.detail;
  if (detail && typeof detail === "object") {
    const message = [detail.message, detail.hint].filter(Boolean).join("\n");
    return new EngineRequestError(message || "Engine request failed.", {
      category: detail.category,
      hint: detail.hint,
      operation: detail.operation,
      retryable: detail.retryable,
    });
  }
  if (typeof detail === "string" && detail) {
    return new EngineRequestError(detail);
  }
  return new EngineRequestError("Engine request failed.");
}
