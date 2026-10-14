// @ts-check
// Pure journey state transitions and step gating. No DOM, no network: the
// controller (app.js) asks these functions what is allowed and renders it.

import { studioJourneySteps } from "./helpCatalog.js";

/** @typedef {import("./storageAdapter.js").JourneyState} JourneyState */
/** @typedef {import("./generated/schemas/deploy-plan.schema").DeployPlan} DeployPlan */
/** @typedef {import("./generated/schemas/server-profile.schema").ServerProfile} ServerProfile */
/** @typedef {"active" | "done" | "available" | "locked"} StepState */
/**
 * A dry-run plan and the exact request it describes. Held in memory only.
 * @typedef {{ plan: DeployPlan, source: "engine" | "static", signature: string }} Preview
 */
/**
 * What gating needs to know besides the journey itself.
 * @typedef {object} GateContext
 * @property {JourneyState} journey
 * @property {boolean} serverValid
 * @property {boolean} deployValid
 * @property {Preview | null} preview
 * @property {string} requestSignature signature of the current (unconfirmed) request
 * @property {boolean} hasOperation an operation is running or has finished in this tab
 */

export const STEP_IDS = studioJourneySteps.map((step) => step.id);

/** Stable signature of a request: key order never matters. @param {object} request */
export function requestSignature(request) {
  const record = /** @type {Record<string, unknown>} */ (request);
  return JSON.stringify(
    Object.keys(record)
      .sort()
      .map((key) => [key, record[key]]),
  );
}

/**
 * @param {Preview | null} preview
 * @param {string} signature
 */
export function isPreviewCurrent(preview, signature) {
  return Boolean(preview && preview.signature === signature);
}

/**
 * @param {string} step
 * @param {GateContext} ctx
 * @returns {{ allowed: boolean, reason: string }}
 */
export function stepGate(step, ctx) {
  const open = { allowed: true, reason: "" };
  if (!STEP_IDS.includes(step)) {
    return { allowed: false, reason: "Unknown step." };
  }
  if (step === "review") {
    if (!ctx.deployValid) {
      return { allowed: false, reason: "Fix deploy options before previewing changes." };
    }
    if (!ctx.preview) {
      return { allowed: false, reason: "Run Preview changes so Studio has real plan evidence for this tab." };
    }
    if (!isPreviewCurrent(ctx.preview, ctx.requestSignature)) {
      return { allowed: false, reason: "Deploy options changed. Preview changes again before review." };
    }
    return open;
  }
  if (step === "welcome" || step === "server" || STEP_IDS.indexOf(step) <= STEP_IDS.indexOf(ctx.journey.currentStep)) {
    return open;
  }
  if (step === "connect" || step === "keys") {
    return ctx.serverValid
      ? open
      : { allowed: false, reason: "Enter a valid public IP, SSH username, and SSH port first." };
  }
  if (step === "deploy") {
    return ctx.journey.readiness.sshValidated || ctx.journey.readiness.keyReady
      ? open
      : { allowed: false, reason: "Meridian needs a confirmed server connection before deploy options matter." };
  }
  if (step === "complete") {
    return ctx.hasOperation ? open : { allowed: false, reason: "Start a deploy to see its progress here." };
  }
  return open;
}

/**
 * @param {string} step
 * @param {GateContext} ctx
 * @returns {StepState}
 */
export function stepProgress(step, ctx) {
  const { journey } = ctx;
  if (step === journey.currentStep) return "active";
  if (step === "welcome") return "done";
  if (step === "server") return journey.readiness.serverSaved ? "done" : "available";
  const done =
    (step === "connect" && journey.readiness.sshValidated) ||
    (step === "keys" && journey.readiness.keyReady) ||
    (step === "deploy" && isPreviewCurrent(ctx.preview, ctx.requestSignature));
  if (done) return "done";
  return stepGate(step, ctx).allowed ? "available" : "locked";
}

/** @param {StepState} state */
export function stepStateLabel(state) {
  if (state === "active") return "Current step";
  if (state === "done") return "Done";
  if (state === "locked") return "Locked";
  return "Ready";
}

/**
 * Edit server fields. Changing how Meridian reaches the server (host, user,
 * port) drops the saved-server selection and every readiness it proved.
 * @param {JourneyState} journey
 * @param {Partial<JourneyState["server"]>} patch
 * @returns {JourneyState}
 */
export function editServer(journey, patch) {
  const server = { ...journey.server, ...patch };
  const reachChanged = ["host", "ssh_user", "ssh_port"].some(
    (key) => key in patch && /** @type {Record<string, string>} */ (patch)[key] !== /** @type {Record<string, string>} */ (journey.server)[key],
  );
  if (!reachChanged) {
    return { ...journey, server };
  }
  return {
    ...journey,
    server,
    selectedServerId: "",
    readiness: { keyReady: false, serverSaved: false, sshValidated: false },
  };
}

/**
 * @param {JourneyState} journey
 * @param {Partial<JourneyState["deploy"]>} patch
 * @returns {JourneyState}
 */
export function editDeploy(journey, patch) {
  return { ...journey, deploy: { ...journey.deploy, ...patch } };
}

/**
 * Use a saved server profile: its readiness comes from what the Engine recorded.
 * @param {JourneyState} journey
 * @param {Pick<ServerProfile, "id" | "title" | "host" | "ssh_user" | "ssh_port" | "auth_state">} server
 * @returns {JourneyState}
 */
export function selectSavedServer(journey, server) {
  const checked = server.auth_state === "validated" || server.auth_state === "key_ready";
  return {
    ...journey,
    selectedServerId: server.id,
    readiness: {
      serverSaved: true,
      sshValidated: checked,
      keyReady: server.auth_state === "key_ready",
    },
    server: {
      title: server.title,
      host: server.host,
      ssh_user: server.ssh_user ?? journey.server.ssh_user,
      ssh_port: String(server.ssh_port ?? journey.server.ssh_port),
    },
  };
}

/**
 * Fold an Engine response into the journey's readiness.
 * @param {JourneyState} journey
 * @param {any} payload
 * @returns {JourneyState}
 */
export function recordPayload(journey, payload) {
  const readiness = { ...journey.readiness };
  let selectedServerId = journey.selectedServerId;
  if (payload?.schema === "meridian.server-profile/v1" && payload.server) {
    readiness.serverSaved = true;
    selectedServerId = payload.server.id || selectedServerId;
  } else if (payload?.schema === "meridian.server-validate/v1" && payload.result) {
    readiness.sshValidated = Boolean(payload.result.auth_ok && payload.result.sudo_ok);
    selectedServerId = payload.result.server?.id || selectedServerId;
  } else if (payload?.schema === "meridian.server-bootstrap-key/v1" && payload.result) {
    readiness.keyReady = Boolean(payload.result.verified);
    readiness.sshValidated = readiness.keyReady || readiness.sshValidated;
    selectedServerId = payload.result.server?.id || selectedServerId;
  } else {
    return journey;
  }
  return { ...journey, readiness, selectedServerId };
}

/**
 * Whether Start deploy may run.
 * @param {{ engineReady: boolean, deployValid: boolean, confirmed: boolean, preview: Preview | null, requestSignature: string }} input
 */
export function canStartDeploy({ engineReady, deployValid, confirmed, preview, requestSignature: signature }) {
  return engineReady && deployValid && confirmed && isPreviewCurrent(preview, signature);
}
