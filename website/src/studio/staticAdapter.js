const IPV4_RE = /^(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;

const FIELD_LABELS = {
  client_name: "First client",
  ip: "Server IP address",
  sni: "Camouflage target",
  user: "SSH user",
};

export function initialFormState(workflow) {
  const state = {};
  for (const field of workflow.fields) {
    state[field.id] = field.default ?? (field.kind === "boolean" || field.kind === "confirmation" ? false : "");
  }
  return state;
}

export function buildDeployRequest(formState) {
  const request = {};
  for (const [key, rawValue] of Object.entries(formState)) {
    if (key === "confirm") {
      request.yes = Boolean(rawValue);
      continue;
    }
    if (typeof rawValue === "string") {
      request[key] = rawValue.trim();
    } else {
      request[key] = rawValue;
    }
  }
  if (!("yes" in request)) {
    request.yes = false;
  }
  return request;
}

export function validateDeployRequest(request, schema, workflow) {
  const errors = [];
  const requiredFields = workflow.fields.filter((field) => field.required && field.id !== "confirm");

  for (const field of requiredFields) {
    const value = request[field.id];
    if (value === undefined || value === null || String(value).trim() === "") {
      errors.push({ field: field.id, message: `${field.label} is required.` });
    }
  }

  for (const [field, propertySchema] of Object.entries(schema.properties ?? {})) {
    const value = request[field];
    if (value === undefined || value === null || value === "") {
      continue;
    }
    const message = validateSchemaProperty(String(value), propertySchema, field);
    if (message) {
      errors.push({ field, message });
    }
  }

  return dedupeErrors(errors);
}

export function buildCliCommands() {
  return {
    deploy: "meridian deploy --request deploy.json --json --events=jsonl",
    dryRun: "meridian deploy --request deploy.json --dry-run --json",
  };
}

export function parseEnvelope(text) {
  const payload = JSON.parse(text.trim());
  if (!payload || typeof payload !== "object" || payload.schema !== "meridian.output/v1") {
    throw new Error("Expected a meridian.output/v1 JSON envelope.");
  }
  return payload;
}

export function parseEventStream(text) {
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const events = lines.map((line) => JSON.parse(line));
  for (const event of events) {
    if (!event || typeof event !== "object" || event.schema !== "meridian.event/v1") {
      throw new Error("Expected meridian.event/v1 JSONL events.");
    }
  }
  return events;
}

export function formatJson(value) {
  return JSON.stringify(value, null, 2);
}

export function summarizeEnvelope(envelope) {
  const text = envelope.summary?.text || `${envelope.command || "Command"} ${envelope.status || "finished"}`;
  return {
    changed: Boolean(envelope.summary?.changed),
    status: envelope.status || "unknown",
    text,
  };
}

export function timelineFromEvents(events) {
  return events.map((event) => ({
    data: event.data ?? {},
    level: event.level ?? "info",
    message: event.message ?? event.type,
    phase: event.phase ?? "",
    seq: event.seq ?? 0,
    type: event.type ?? "event",
  }));
}

function validateSchemaProperty(value, schema, field) {
  if (schema.pattern && !new RegExp(schema.pattern).test(value)) {
    return patternMessage(field);
  }
  if (Number.isInteger(schema.minLength) && value.length < schema.minLength) {
    return `${labelFor(field)} is required.`;
  }
  if (Array.isArray(schema.anyOf) && !schema.anyOf.some((candidate) => matchesSchemaCandidate(value, candidate))) {
    return patternMessage(field);
  }
  return "";
}

function matchesSchemaCandidate(value, schema) {
  if (schema.format === "ipv4") {
    return IPV4_RE.test(value);
  }
  if (schema.format === "ipv6") {
    return isLikelyIpv6(value);
  }
  if (schema.pattern) {
    return new RegExp(schema.pattern).test(value);
  }
  return true;
}

function isLikelyIpv6(value) {
  if (!value.includes(":") || !/^[0-9a-fA-F:]+$/.test(value)) {
    return false;
  }
  if (value.includes("::")) {
    return value.split("::").length === 2;
  }
  const groups = value.split(":");
  return groups.length >= 3 && groups.length <= 8 && groups.every((group) => group.length > 0 && group.length <= 4);
}

function patternMessage(field) {
  if (field === "ip") {
    return "Enter a valid IP address, or use local when running Meridian on the target server.";
  }
  if (field === "user") {
    return "Use letters, numbers, dots, hyphens, and underscores.";
  }
  if (field === "client_name") {
    return "Use letters, numbers, hyphens, and underscores.";
  }
  return `${labelFor(field)} has an invalid format.`;
}

function labelFor(field) {
  return FIELD_LABELS[field] ?? field.replaceAll("_", " ");
}

function dedupeErrors(errors) {
  const seen = new Set();
  return errors.filter((error) => {
    const key = `${error.field}:${error.message}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}
