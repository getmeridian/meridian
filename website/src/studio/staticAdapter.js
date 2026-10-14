// @ts-check
// Pure request builders, validators, CLI commands, and pasted-output parsers.
// Browser-safe and Node-tested; no DOM.

import { workflowDefaults, workflowLabels } from "./contractDefaults.js";
import {
  deployRequestSchema,
  deployWorkflow,
  serverConnectionDraftSchema,
  serverOnboardingWorkflow,
} from "./generated/index.js";

/** @typedef {import("./generated/schemas/deploy-request.schema").DeployRequest} DeployRequest */
/** @typedef {import("./generated/schemas/server-connection-draft.schema").ServerConnectionDraft} ServerConnectionDraft */
/** @typedef {import("./storageAdapter.js").JourneyState} JourneyState */
/** @typedef {import("./contractDefaults.js").Workflow} Workflow */
/**
 * @typedef {object} JsonSchema
 * @property {Record<string, any>} [properties]
 * @property {boolean} [additionalProperties]
 */
/** @typedef {{ field: string, label: string, message: string }} FieldError */
/** @typedef {Pick<JourneyState, "deploy" | "server" | "selectedServerId"> & { confirmDeploy?: boolean }} RequestSource */

const IPV4_RE = /^(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;

const FIELD_LABELS = {
  requested_server: "Saved server",
  ...workflowLabels(deployWorkflow, serverOnboardingWorkflow),
};

const DEPLOY_DEFAULTS = workflowDefaults(deployWorkflow);
const SERVER_DEFAULTS = workflowDefaults(serverOnboardingWorkflow);

// Deploy options the journey edits. Each must be a DeployRequest property: the
// Engine model forbids extra keys, so a stale field turns every request into a 422.
/** @type {ReadonlyArray<keyof JourneyState["deploy"]>} */
export const JOURNEY_DEPLOY_FIELDS = [
  "client_name",
  "color",
  "domain",
  "geo_block",
  "harden",
  "icon",
  "server_name",
  "sni",
  "warp",
];

// `meridian deploy` flag for each journey option: a string flag takes the value,
// a [on, off] pair is a boolean switch.
/** @type {Record<string, string | [string, string]>} */
export const DEPLOY_FLAGS = {
  client_name: "--client-name",
  color: "--color",
  domain: "--domain",
  geo_block: ["--geo-block", "--no-geo-block"],
  harden: ["--harden", "--no-harden"],
  icon: "--icon",
  server_name: "--display-name",
  sni: "--sni",
  warp: ["--warp", "--no-warp"],
};

// The one DeployRequest builder: Engine dry-run/deploy, the downloaded
// deploy.json, and the copied commands all come from here. Empty fields take
// the workflow contract's defaults at build time, so a cleared input stays
// cleared in the draft.
/**
 * @param {RequestSource} journey
 * @param {JsonSchema} [schema]
 * @returns {DeployRequest}
 */
export function buildDeployRequestFromJourney(journey, schema = deployRequestSchema) {
  /** @type {Record<string, unknown>} */
  const deploy = journey.deploy ?? {};
  /** @type {Record<string, unknown>} */
  const request = {};
  for (const key of JOURNEY_DEPLOY_FIELDS) {
    const fallback = DEPLOY_DEFAULTS[key];
    const raw = deploy[key];
    if (typeof fallback === "boolean") {
      request[key] = typeof raw === "boolean" ? raw : fallback;
    } else {
      request[key] = String(raw ?? "").trim() || String(fallback ?? "");
    }
  }
  request.yes = Boolean(journey.confirmDeploy);
  if (journey.selectedServerId) {
    request.requested_server = String(journey.selectedServerId);
  } else {
    request.ip = String(journey.server?.host ?? "").trim();
    request.user = String(journey.server?.ssh_user ?? "").trim() || String(SERVER_DEFAULTS.ssh_user ?? "root");
    request.ssh_port = sshPortValue(journey.server?.ssh_port);
  }
  return pickSchemaProperties(request, schema);
}

// Keep only what the generated schema declares, so no request carries a key
// the Engine would reject.
/**
 * @param {Record<string, unknown>} request
 * @param {JsonSchema} schema
 */
export function pickSchemaProperties(request, schema) {
  const allowed = schema.properties ?? {};
  return Object.fromEntries(Object.entries(request).filter(([key]) => key in allowed));
}

/**
 * Server draft with contract defaults applied (port, user) and an automatic
 * title when the field is empty.
 * @param {Partial<Record<keyof JourneyState["server"], unknown>>} formState
 * @returns {ServerConnectionDraft}
 */
export function buildServerConnectionDraft(formState) {
  const host = String(formState.host ?? "").trim();
  const title = String(formState.title ?? "").trim() || autoServerTitle(host);
  return {
    title,
    host,
    ssh_user: String(formState.ssh_user ?? "").trim() || String(SERVER_DEFAULTS.ssh_user ?? "root"),
    ssh_port: sshPortValue(formState.ssh_port),
  };
}

/** @param {unknown} value */
function sshPortValue(value) {
  const raw = String(value ?? "").trim();
  if (!raw) {
    return Number.parseInt(String(SERVER_DEFAULTS.ssh_port ?? "22"), 10);
  }
  const parsed = Number(raw);
  return Number.isInteger(parsed) ? parsed : Number.NaN;
}

/**
 * @param {object} request
 * @param {JsonSchema} [schema]
 * @param {Workflow} [workflow]
 */
export function validateDeployRequest(request, schema = deployRequestSchema, workflow = deployWorkflow) {
  return validateRequest(request, schema, workflow);
}

/**
 * @param {object} request
 * @param {JsonSchema} [schema]
 * @param {Workflow} [workflow]
 */
export function validateServerConnectionDraft(
  request,
  schema = serverConnectionDraftSchema,
  workflow = serverOnboardingWorkflow,
) {
  return validateRequest(request, schema, workflow);
}

/**
 * @param {object} input
 * @param {JsonSchema} schema
 * @param {Workflow} workflow
 * @returns {FieldError[]}
 */
function validateRequest(input, schema, workflow) {
  const request = /** @type {Record<string, unknown>} */ (input);
  /** @type {FieldError[]} */
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

  if (schema.additionalProperties === false) {
    const known = schema.properties ?? {};
    for (const field of Object.keys(request)) {
      if (!(field in known)) {
        errors.push({ field, label: labelFor(field), message: `${labelFor(field)} is not an option this Meridian version accepts.` });
      }
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

/**
 * @param {string} command
 * @returns {{ host: string, ssh_port: number, ssh_user: string, title: string }}
 */
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

/**
 * The `meridian deploy` command a person would type for this request. Flags
 * appear only where the request differs from what the CLI does on its own, so
 * running it deploys exactly what Studio would.
 * @param {DeployRequest} request
 * @param {{ dryRun?: boolean, schema?: JsonSchema }} [options]
 */
export function buildDeployCommand(request, options = {}) {
  const properties = (options.schema ?? deployRequestSchema).properties ?? {};
  const words = ["meridian", "deploy"];
  if (request.requested_server) {
    words.push(shellQuote(request.requested_server));
  } else {
    words.push(shellQuote(request.ip ?? ""));
    if (request.user && request.user !== "root") words.push("--user", shellQuote(request.user));
    if (request.ssh_port && request.ssh_port !== 22) words.push("--ssh-port", String(request.ssh_port));
  }
  const values = /** @type {Record<string, unknown>} */ (/** @type {unknown} */ (request));
  for (const key of JOURNEY_DEPLOY_FIELDS) {
    const value = values[key];
    if (value === undefined || value === properties[key]?.default) continue;
    const flag = DEPLOY_FLAGS[key];
    if (Array.isArray(flag)) {
      words.push(value ? flag[0] : flag[1]);
    } else if (value !== "") {
      words.push(flag, shellQuote(String(value)));
    }
  }
  if (options.dryRun) words.push("--dry-run");
  return words.join(" ");
}

/**
 * @param {{ deployRequest: DeployRequest, serverDraft: ServerConnectionDraft }} input
 */
export function buildCommandPack({ deployRequest, serverDraft }) {
  const serverCommands = buildServerCliCommands(serverDraft);
  return {
    deploy: buildDeployCommand(deployRequest),
    dryRun: buildDeployCommand(deployRequest, { dryRun: true }),
    requestDryRun: "meridian deploy --request deploy.json --dry-run",
    saveServer: serverCommands.save,
    setupKey: serverCommands.copyKey,
    testSsh: serverCommands.connect,
  };
}

/**
 * @param {ServerConnectionDraft} draft
 */
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
    save: `meridian server add ${shellQuote(draft.host)} --name ${shellQuote(cliName)} --user ${shellQuote(draft.ssh_user ?? "root")}${addPort}`,
  };
}

/**
 * @param {string} text
 * @returns {any}
 */
export function parseEnvelope(text) {
  const payload = JSON.parse(text.trim());
  if (!payload || typeof payload !== "object" || payload.schema !== "meridian.output/v1") {
    throw new Error("Expected a meridian.output/v1 JSON envelope.");
  }
  return payload;
}

/**
 * @param {string} text
 * @returns {any[]}
 */
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

/** @param {unknown} value */
export function formatJson(value) {
  return JSON.stringify(value, null, 2);
}

/**
 * @param {unknown} value
 * @param {Record<string, any>} schema
 * @param {string} field
 */
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
  if (Array.isArray(schema.anyOf) && !schema.anyOf.some((/** @type {Record<string, any>} */ candidate) => matchesSchemaCandidate(text, candidate))) {
    return patternMessage(field);
  }
  return "";
}

/**
 * @param {unknown} value
 * @param {Record<string, any>} schema
 * @param {string} field
 */
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

/**
 * @param {string} value
 * @param {Record<string, any>} schema
 */
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

/** @param {string} value */
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

/** @param {string} field */
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

/** @param {string} value */
function shellQuote(value) {
  const text = String(value);
  if (/^[a-zA-Z0-9_./:@%+=,-]+$/.test(text)) {
    return text;
  }
  return `'${text.replaceAll("'", "'\"'\"'")}'`;
}

/** @param {string} command */
function shellWords(command) {
  /** @type {string[]} */
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

/** @param {string} title */
function slugifyTitle(title) {
  const slug = String(title)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "server";
}

/** @param {string} host */
function autoServerTitle(host) {
  return host ? `Server ${host}` : "New server";
}

/** @param {string} field */
export function labelFor(field) {
  return /** @type {Record<string, string>} */ (FIELD_LABELS)[field] ?? field.replaceAll("_", " ");
}

/** @param {FieldError[]} errors */
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
