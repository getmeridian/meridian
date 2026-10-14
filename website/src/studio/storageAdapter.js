// @ts-check
// Versioned, non-secret Studio draft in localStorage. Field values are kept
// exactly as typed (a cleared input stays cleared); defaults are applied when a
// request is built (staticAdapter.js).

import { studioJourneySteps } from "./helpCatalog.js";
import { deployWorkflow, serverOnboardingWorkflow } from "./generated/index.js";
import { workflowDefaults } from "./contractDefaults.js";

export const STUDIO_DRAFT_KEY = "meridian.studio.v1.draft";

const STEPS = new Set(studioJourneySteps.map((step) => step.id));

/**
 * @typedef {object} ServerFields
 * @property {string} title
 * @property {string} host
 * @property {string} ssh_user
 * @property {string} ssh_port raw input; parsed when the draft is built
 */
/**
 * @typedef {object} DeployFields
 * @property {string} client_name
 * @property {string} color
 * @property {string} domain
 * @property {boolean} geo_block
 * @property {boolean} harden
 * @property {string} icon
 * @property {string} server_name
 * @property {string} sni
 * @property {boolean} warp
 */
/**
 * @typedef {object} JourneyState
 * @property {1} version
 * @property {string} currentStep
 * @property {string} activeOperationId
 * @property {string} selectedServerId
 * @property {{ keyReady: boolean, serverSaved: boolean, sshValidated: boolean }} readiness
 * @property {ServerFields} server
 * @property {DeployFields} deploy
 * @property {boolean} advancedOpen
 */

/** @returns {JourneyState} */
export function createDefaultJourneyState() {
  const serverDefaults = workflowDefaults(serverOnboardingWorkflow, { title: "" });
  const deployDefaults = workflowDefaults(deployWorkflow);
  return {
    version: 1,
    currentStep: "welcome",
    activeOperationId: "",
    selectedServerId: "",
    readiness: {
      keyReady: false,
      serverSaved: false,
      sshValidated: false,
    },
    server: {
      title: String(serverDefaults.title ?? ""),
      host: String(serverDefaults.host ?? ""),
      ssh_user: String(serverDefaults.ssh_user ?? ""),
      ssh_port: String(serverDefaults.ssh_port ?? ""),
    },
    deploy: {
      client_name: String(deployDefaults.client_name ?? ""),
      color: String(deployDefaults.color ?? ""),
      domain: String(deployDefaults.domain ?? ""),
      geo_block: Boolean(deployDefaults.geo_block),
      harden: Boolean(deployDefaults.harden),
      icon: String(deployDefaults.icon ?? ""),
      server_name: String(deployDefaults.server_name ?? ""),
      sni: String(deployDefaults.sni ?? ""),
      warp: Boolean(deployDefaults.warp),
    },
    advancedOpen: false,
  };
}

// Only these keys survive; anything else (passwords, keys, pasted output, and a
// dry-run "previewed" flag without the plan it refers to) is dropped.
/**
 * @param {unknown} value
 * @returns {JourneyState}
 */
export function sanitizeJourneyDraft(value) {
  const fallback = createDefaultJourneyState();
  if (!value || typeof value !== "object") {
    return fallback;
  }
  /** @type {Record<string, any>} */
  const draft = value;
  const server = objectValue(draft.server);
  const deploy = objectValue(draft.deploy);
  const readiness = objectValue(draft.readiness);
  return {
    version: 1,
    currentStep: STEPS.has(draft.currentStep) ? draft.currentStep : fallback.currentStep,
    activeOperationId: stringValue(draft.activeOperationId, fallback.activeOperationId),
    selectedServerId: stringValue(draft.selectedServerId, fallback.selectedServerId),
    readiness: {
      keyReady: booleanValue(readiness.keyReady, fallback.readiness.keyReady),
      serverSaved: booleanValue(readiness.serverSaved, fallback.readiness.serverSaved),
      sshValidated: booleanValue(readiness.sshValidated, fallback.readiness.sshValidated),
    },
    server: {
      host: stringValue(server.host, fallback.server.host),
      ssh_port: portText(server.ssh_port, fallback.server.ssh_port),
      ssh_user: stringValue(server.ssh_user, fallback.server.ssh_user),
      title: stringValue(server.title, fallback.server.title),
    },
    deploy: {
      client_name: stringValue(deploy.client_name, fallback.deploy.client_name),
      color: stringValue(deploy.color, fallback.deploy.color),
      domain: stringValue(deploy.domain, fallback.deploy.domain),
      geo_block: booleanValue(deploy.geo_block, fallback.deploy.geo_block),
      harden: booleanValue(deploy.harden, fallback.deploy.harden),
      icon: stringValue(deploy.icon, fallback.deploy.icon),
      server_name: stringValue(deploy.server_name, fallback.deploy.server_name),
      sni: stringValue(deploy.sni, fallback.deploy.sni),
      warp: booleanValue(deploy.warp, fallback.deploy.warp),
    },
    advancedOpen: booleanValue(draft.advancedOpen, fallback.advancedOpen),
  };
}

/**
 * @param {Pick<Storage, "setItem"> | null | undefined} storage
 * @param {unknown} value
 */
export function saveJourneyDraft(storage, value) {
  if (!storage) {
    return false;
  }
  storage.setItem(STUDIO_DRAFT_KEY, JSON.stringify(sanitizeJourneyDraft(value)));
  return true;
}

/**
 * @param {Pick<Storage, "getItem"> | null | undefined} storage
 * @returns {JourneyState}
 */
export function loadJourneyDraft(storage) {
  if (!storage) {
    return createDefaultJourneyState();
  }
  const raw = storage.getItem(STUDIO_DRAFT_KEY);
  if (!raw) {
    return createDefaultJourneyState();
  }
  try {
    return sanitizeJourneyDraft(JSON.parse(raw));
  } catch {
    return createDefaultJourneyState();
  }
}

/**
 * @param {Pick<Storage, "removeItem"> | null | undefined} storage
 * @returns {JourneyState}
 */
export function clearJourneyDraft(storage) {
  if (storage) {
    storage.removeItem(STUDIO_DRAFT_KEY);
  }
  return createDefaultJourneyState();
}

/** @param {unknown} value @returns {Record<string, any>} */
function objectValue(value) {
  return value && typeof value === "object" ? /** @type {Record<string, any>} */ (value) : {};
}

/** @param {unknown} value @param {string} fallback */
function stringValue(value, fallback) {
  return typeof value === "string" ? value : fallback;
}

/** @param {unknown} value @param {boolean} fallback */
function booleanValue(value, fallback) {
  return typeof value === "boolean" ? value : fallback;
}

// Older drafts stored the port as a number.
/** @param {unknown} value @param {string} fallback */
function portText(value, fallback) {
  if (typeof value === "number" && Number.isInteger(value)) return String(value);
  return typeof value === "string" ? value : fallback;
}
