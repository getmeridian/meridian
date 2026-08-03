export const STUDIO_DRAFT_KEY = "meridian.studio.v1.draft";

import { deployWorkflow, serverOnboardingWorkflow } from "./generated/index.js";
import { workflowDefaults } from "./contractDefaults.js";

const STEPS = new Set(["welcome", "server", "connect", "keys", "deploy", "review", "complete"]);

export function createDefaultJourneyState() {
  const serverDefaults = workflowDefaults(serverOnboardingWorkflow, { title: "" });
  const deployDefaults = workflowDefaults(deployWorkflow);
  return {
    version: 1,
    currentStep: "welcome",
    activeOperationId: "",
    mode: "static",
    selectedServerId: "",
    lastDryRunSignature: "",
    readiness: {
      dryRunComplete: false,
      keyReady: false,
      serverSaved: false,
      sshValidated: false,
    },
    server: {
      title: serverDefaults.title,
      host: serverDefaults.host,
      ssh_user: serverDefaults.ssh_user,
      ssh_port: Number(serverDefaults.ssh_port),
    },
    deploy: {
      client_name: deployDefaults.client_name,
      color: deployDefaults.color,
      domain: deployDefaults.domain,
      geo_block: deployDefaults.geo_block,
      harden: deployDefaults.harden,
      icon: deployDefaults.icon,
      pq: deployDefaults.pq,
      server_name: deployDefaults.server_name,
      sni: deployDefaults.sni,
      warp: deployDefaults.warp,
    },
    advancedOpen: false,
  };
}

export function sanitizeJourneyDraft(value) {
  const fallback = createDefaultJourneyState();
  if (!value || typeof value !== "object") {
    return fallback;
  }
  const server = value.server && typeof value.server === "object" ? value.server : {};
  const deploy = value.deploy && typeof value.deploy === "object" ? value.deploy : {};
  const readiness = value.readiness && typeof value.readiness === "object" ? value.readiness : {};
  return {
    currentStep: STEPS.has(value.currentStep) ? value.currentStep : fallback.currentStep,
    activeOperationId: stringValue(value.activeOperationId, fallback.activeOperationId),
    lastDryRunSignature: stringValue(value.lastDryRunSignature, fallback.lastDryRunSignature),
    mode: value.mode === "engine" ? "engine" : "static",
    selectedServerId: stringValue(value.selectedServerId, fallback.selectedServerId),
    version: 1,
    readiness: {
      dryRunComplete: booleanValue(readiness.dryRunComplete, fallback.readiness.dryRunComplete),
      keyReady: booleanValue(readiness.keyReady, fallback.readiness.keyReady),
      serverSaved: booleanValue(readiness.serverSaved, fallback.readiness.serverSaved),
      sshValidated: booleanValue(readiness.sshValidated, fallback.readiness.sshValidated),
    },
    server: {
      host: stringValue(server.host, fallback.server.host),
      ssh_port: portValue(server.ssh_port, fallback.server.ssh_port),
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
      pq: booleanValue(deploy.pq, fallback.deploy.pq),
      server_name: stringValue(deploy.server_name, fallback.deploy.server_name),
      sni: stringValue(deploy.sni, fallback.deploy.sni),
      warp: booleanValue(deploy.warp, fallback.deploy.warp),
    },
    advancedOpen: booleanValue(value.advancedOpen, fallback.advancedOpen),
  };
}

export function saveJourneyDraft(storage, value) {
  if (!storage) {
    return false;
  }
  storage.setItem(STUDIO_DRAFT_KEY, JSON.stringify(sanitizeJourneyDraft(value)));
  return true;
}

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

export function clearJourneyDraft(storage) {
  if (storage) {
    storage.removeItem(STUDIO_DRAFT_KEY);
  }
  return createDefaultJourneyState();
}

function stringValue(value, fallback) {
  return typeof value === "string" ? value : fallback;
}

function booleanValue(value, fallback) {
  return typeof value === "boolean" ? value : fallback;
}

function portValue(value, fallback) {
  const port = typeof value === "number" ? value : Number.parseInt(String(value ?? ""), 10);
  return Number.isInteger(port) && port >= 1 && port <= 65535 ? port : fallback;
}
