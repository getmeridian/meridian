const IPV4_RE = /^(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;

const FIELD_LABELS = {
  client_name: "First client",
  geo_block: "Block RU traffic",
  host: "Server IP address",
  ip: "Server IP address",
  requested_server: "Saved server",
  server_name: "Connection page name",
  sni: "Camouflage target",
  ssh_port: "SSH port",
  ssh_user: "SSH user",
  title: "Server title",
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

export function buildDeployRequestFromJourney(journey) {
  const request = {
    client_name: String(journey.deploy?.client_name ?? "default").trim() || "default",
    color: String(journey.deploy?.color ?? "ocean").trim() || "ocean",
    domain: String(journey.deploy?.domain ?? "").trim(),
    geo_block: Boolean(journey.deploy?.geo_block ?? true),
    harden: Boolean(journey.deploy?.harden ?? true),
    icon: String(journey.deploy?.icon ?? "").trim(),
    pq: Boolean(journey.deploy?.pq ?? false),
    server_name: String(journey.deploy?.server_name ?? "My VPN").trim() || "My VPN",
    sni: String(journey.deploy?.sni ?? "www.microsoft.com").trim() || "www.microsoft.com",
    warp: Boolean(journey.deploy?.warp ?? false),
    yes: Boolean(journey.confirmDeploy),
  };
  if (journey.selectedServerId) {
    request.requested_server = String(journey.selectedServerId);
  } else {
    request.ip = String(journey.server?.host ?? "").trim();
    request.user = String(journey.server?.ssh_user ?? "root").trim() || "root";
    request.ssh_port = Number(journey.server?.ssh_port ?? 22);
  }
  return request;
}

export function buildServerConnectionDraft(formState) {
  const rawPort = String(formState.ssh_port ?? "").trim();
  const parsedPort = Number.parseInt(rawPort, 10);
  const host = String(formState.host ?? "").trim();
  const title = String(formState.title ?? "").trim() || autoServerTitle(host);
  return {
    title,
    host,
    ssh_user: String(formState.ssh_user ?? "root").trim(),
    ssh_port: Number.isNaN(parsedPort) ? rawPort : parsedPort,
  };
}

export function validateDeployRequest(request, schema, workflow) {
  return validateRequest(request, schema, workflow);
}

export function validateServerConnectionDraft(request, schema, workflow) {
  return validateRequest(request, schema, workflow);
}

function validateRequest(request, schema, workflow) {
  const errors = [];
  const requiredFields = workflow.fields.filter((field) => field.required && field.id !== "confirm");
  const requestHasServerReference = typeof request.requested_server === "string" && request.requested_server.trim() !== "";

  for (const field of requiredFields) {
    if (requestHasServerReference && (field.id === "ip" || field.id === "user")) {
      continue;
    }
    const value = request[field.id];
    if (value === undefined || value === null || String(value).trim() === "") {
      errors.push({ field: field.id, label: field.label, message: `${field.label} is required.` });
    }
  }

  for (const [field, propertySchema] of Object.entries(schema.properties ?? {})) {
    const value = request[field];
    if (value === undefined || value === null || value === "") {
      continue;
    }
    const message = validateSchemaProperty(value, propertySchema, field);
    if (message) {
      errors.push({ field, label: labelFor(field), message });
    }
  }

  return dedupeErrors(errors);
}

export function parseSshCommand(command) {
  const tokens = shellWords(String(command ?? "").trim());
  if (tokens[0] !== "ssh") {
    throw new Error("Paste a command that starts with ssh.");
  }
  let port = 22;
  let target = "";
  let user = "";
  for (let index = 1; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === "-p" && tokens[index + 1]) {
      port = Number.parseInt(tokens[index + 1], 10);
      index += 1;
      continue;
    }
    if (token.startsWith("-p") && token.length > 2) {
      port = Number.parseInt(token.slice(2), 10);
      continue;
    }
    if (token === "-l" && tokens[index + 1]) {
      user = tokens[index + 1];
      index += 1;
      continue;
    }
    if (token.startsWith("-l") && token.length > 2) {
      user = token.slice(2);
      continue;
    }
    if (token === "-o" && tokens[index + 1]) {
      const [name, value] = tokens[index + 1].split("=", 2);
      if (name?.toLowerCase() === "port") {
        port = Number.parseInt(value, 10);
      }
      index += 1;
      continue;
    }
    if (token.startsWith("-o") && token.includes("=")) {
      const [name, value] = token.slice(2).split("=", 2);
      if (name?.toLowerCase() === "port") {
        port = Number.parseInt(value, 10);
      }
      continue;
    }
    if (["-i", "-F", "-J"].includes(token) && tokens[index + 1]) {
      index += 1;
      continue;
    }
    if (!token.startsWith("-")) {
      target = token;
    }
  }
  const [userPart, rawHostPart] = target.includes("@") ? target.split("@") : [user || "root", target];
  const hostPart = rawHostPart.startsWith("[") && rawHostPart.endsWith("]") ? rawHostPart.slice(1, -1) : rawHostPart;
  if (!userPart || !hostPart) {
    throw new Error("Could not find user@server in that SSH command.");
  }
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("SSH port must be between 1 and 65535.");
  }
  return {
    host: hostPart,
    ssh_port: port,
    ssh_user: userPart,
    title: `Server ${hostPart}`,
  };
}

export function buildCliCommands() {
  return {
    deploy: "meridian deploy --request deploy.json --json --events=jsonl",
    dryRun: "meridian deploy --request deploy.json --dry-run --json",
  };
}

export function buildCommandPack({ deployRequest, serverDraft }) {
  const serverCommands = buildServerCliCommands(serverDraft);
  const deployCommands = buildCliCommands();
  return {
    deploy: deployCommands.deploy,
    dryRun: deployCommands.dryRun,
    saveServer: serverCommands.save,
    setupKey: serverCommands.copyKey,
    testSsh: serverCommands.connect,
  };
}

export function buildServerCliCommands(draft) {
  const target = `${draft.ssh_user}@${draft.host}`;
  const port = Number(draft.ssh_port);
  const customPort = Number.isInteger(port) && port !== 22;
  const sshPort = customPort ? ` -p ${port}` : "";
  const addPort = customPort ? ` --ssh-port ${port}` : "";
  const cliName = slugifyTitle(draft.title);
  return {
    connect: `ssh${sshPort} ${shellQuote(target)}`,
    copyKey: `ssh-copy-id${sshPort} ${shellQuote(target)}`,
    save: `meridian server add ${shellQuote(draft.host)} --name ${shellQuote(cliName)} --user ${shellQuote(draft.ssh_user)}${addPort}`,
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
  const firstError = Array.isArray(envelope.errors) ? envelope.errors[0] : null;
  const text =
    (firstError && [firstError.message, firstError.hint].filter(Boolean).join(" ")) ||
    envelope.summary?.text ||
    `${envelope.command || "Command"} ${envelope.status || "finished"}`;
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
  if (schema.type === "integer") {
    return validateIntegerProperty(value, schema, field);
  }

  const text = String(value);
  if (Number.isInteger(schema.maxLength) && text.length > schema.maxLength) {
    return `${labelFor(field)} must be ${schema.maxLength} characters or fewer.`;
  }
  if (schema.pattern && !new RegExp(schema.pattern).test(text)) {
    return patternMessage(field);
  }
  if (Number.isInteger(schema.minLength) && text.length < schema.minLength) {
    return `${labelFor(field)} is required.`;
  }
  if (Array.isArray(schema.enum) && !schema.enum.includes(value)) {
    return `${labelFor(field)} has an invalid option.`;
  }
  if (Array.isArray(schema.anyOf) && !schema.anyOf.some((candidate) => matchesSchemaCandidate(text, candidate))) {
    return patternMessage(field);
  }
  return "";
}

function validateIntegerProperty(value, schema, field) {
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isInteger(number)) {
    return `${labelFor(field)} must be a whole number.`;
  }
  if (Number.isInteger(schema.minimum) && number < schema.minimum) {
    return `${labelFor(field)} must be at least ${schema.minimum}.`;
  }
  if (Number.isInteger(schema.maximum) && number > schema.maximum) {
    return `${labelFor(field)} must be ${schema.maximum} or lower.`;
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
  if (field === "host") {
    return "Enter a valid IP address.";
  }
  if (field === "ip") {
    return "Enter a valid IP address, or use local when running Meridian on the target server.";
  }
  if (field === "ssh_user" || field === "user") {
    return "Use letters, numbers, dots, hyphens, and underscores.";
  }
  if (field === "client_name") {
    return "Use letters, numbers, hyphens, and underscores.";
  }
  return `${labelFor(field)} has an invalid format.`;
}

function shellQuote(value) {
  const text = String(value);
  if (/^[a-zA-Z0-9_./:@%+=,-]+$/.test(text)) {
    return text;
  }
  return `'${text.replaceAll("'", "'\"'\"'")}'`;
}

function shellWords(command) {
  const tokens = [];
  let current = "";
  let quote = "";
  for (let index = 0; index < command.length; index += 1) {
    const char = command[index];
    if (quote) {
      if (char === quote) {
        quote = "";
      } else {
        current += char;
      }
      continue;
    }
    if (char === "'" || char === '"') {
      quote = char;
      continue;
    }
    if (/\s/.test(char)) {
      if (current) {
        tokens.push(current);
        current = "";
      }
      continue;
    }
    current += char;
  }
  if (current) {
    tokens.push(current);
  }
  return tokens;
}

function slugifyTitle(title) {
  const slug = String(title)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "server";
}

function autoServerTitle(host) {
  return host ? `Server ${host}` : "New server";
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
