// @ts-check
// Pure wording for Engine responses, failures, and evidence. One presenter per
// response schema; failures branch on the typed error `category`, never on the
// message text. Views render what these return and nothing else.

import { describeCancellation, operationStateLabel } from "./operationState.js";

/** @typedef {import("./generated/schemas/server-validate-result.schema").ServerValidateResult} ServerValidateResult */
/** @typedef {import("./generated/schemas/server-bootstrap-key-result.schema").ServerBootstrapKeyResult} ServerBootstrapKeyResult */
/** @typedef {import("./generated/schemas/deploy-plan.schema").DeployPlan} DeployPlan */
/** @typedef {import("./generated/schemas/deploy-operation-result.schema").DeployResult} DeployResult */
/** @typedef {import("./generated/schemas/deploy-request.schema").DeployRequest} DeployRequest */
/** @typedef {import("./generated/schemas/operation-snapshot.schema").OperationSnapshot} OperationSnapshot */
/** @typedef {import("./generated/schemas/operation-cancellation.schema").OperationCancellation} OperationCancellation */
/** @typedef {import("./generated/schemas/event.schema").Event} EngineEvent */
/** @typedef {"ok" | "warning" | "error" | "running" | "change" | "quiet"} Tone */
/** @typedef {{ status: string, title: string, detail: string, tone: Tone }} Presentation */

/** @type {Record<string, (payload: any) => Presentation>} */
const PRESENTERS = {
  "meridian.server-profile/v1": (payload) => ({
    status: "saved",
    title: "Server saved",
    detail: `${payload.server.title} saved at ${payload.server.host}:${payload.server.ssh_port}. Check SSH access next.`,
    tone: "ok",
  }),
  "meridian.server-validate/v1": (payload) => {
    /** @type {ServerValidateResult} */
    const result = payload.result;
    if (result.auth_ok && result.sudo_ok) {
      return {
        status: "ready",
        title: "SSH works",
        detail: `SSH and sudo work${result.detected_os ? ` on ${result.detected_os}` : ""}.`,
        tone: "ok",
      };
    }
    return {
      status: "check",
      title: "SSH needs attention",
      detail: (result.hints ?? []).join(" ") || "SSH validation needs attention.",
      tone: "warning",
    };
  },
  "meridian.server-bootstrap-key/v1": (payload) => {
    /** @type {ServerBootstrapKeyResult} */
    const result = payload.result;
    const base = result.verified
      ? "Key login was verified. The password is no longer needed for Studio actions."
      : "Public key was installed, but Studio could not verify private-key login yet.";
    const warnings = result.warnings ?? [];
    return {
      status: result.verified ? "ready" : "check",
      title: result.verified ? "Key login verified" : "Public key installed",
      detail: warnings.length > 0 ? `${base} ${warnings.join(" ")}` : base,
      tone: result.verified ? "ok" : "warning",
    };
  },
  "meridian.deploy-dry-run/v1": (payload) => ({
    status: "preview",
    title: "Preview ready",
    detail: `Preview ready for ${payload.plan.server_ip}. Review the planned changes before deploy.`,
    tone: "ok",
  }),
  "meridian.deploy-operation-result/v1": (payload) => {
    /** @type {DeployResult} */
    const result = payload.result;
    return {
      status: "ready",
      title: result.summary || `Deploy completed for ${result.server_ip}.`,
      detail: result.connection_page_url
        ? "The invite link is ready. Test one device before changing route policy or adding relays."
        : "Deploy finished. Run a connection test before adding relays or more devices.",
      tone: "ok",
    };
  },
  "meridian.output/v1": (envelope) => {
    const firstError = Array.isArray(envelope.errors) ? envelope.errors[0] : null;
    const status = envelope.status || "unknown";
    return {
      status,
      title:
        (firstError && [firstError.message, firstError.hint].filter(Boolean).join(" ")) ||
        envelope.summary?.text ||
        `${envelope.command || "Command"} ${status}`,
      detail: envelope.summary?.changed ? "Changes expected" : "No immediate changes",
      tone: status === "failed" ? "error" : status === "warning" ? "warning" : "ok",
    };
  },
};

/**
 * @param {any} payload any Engine response or pasted envelope
 * @returns {Presentation}
 */
export function presentPayload(payload) {
  const presenter = PRESENTERS[payload?.schema];
  if (presenter) return presenter(payload);
  return { status: "ok", title: "Action complete", detail: payload?.schema || "Action finished.", tone: "ok" };
}

/** Response schemas that have a presenter; anything else is shown generically. */
export const PRESENTED_SCHEMAS = Object.freeze(Object.keys(PRESENTERS));

/**
 * @typedef {object} FailureView
 * @property {string} title
 * @property {string} detail
 * @property {boolean} retryable
 * @property {string} retryLabel
 * @property {boolean} offerDiagnostics
 */

/**
 * What a failed action or deploy means and what to do next, by error category.
 * @param {{ message?: string, hint?: string, category?: string, retryable?: boolean } | null | undefined} error
 * @returns {FailureView}
 */
export function presentFailure(error) {
  const [headline, ...rest] = String(error?.message || "Studio could not finish that.").split("\n");
  const hint = error?.hint || rest.join(" ");
  const category = error?.category || "system";
  if (category === "bug") {
    return {
      title: headline,
      detail: `${hint ? `${hint} ` : ""}This looks like a Meridian bug. Copy the diagnostic report and include it when you report it.`,
      retryable: false,
      retryLabel: "Try deploy again",
      offerDiagnostics: true,
    };
  }
  if (category === "user") {
    return {
      title: headline,
      detail: hint || "Fix what the message names, then preview again before deploying.",
      retryable: error?.retryable ?? true,
      retryLabel: "Try deploy again",
      offerDiagnostics: false,
    };
  }
  if (category === "cancelled") {
    return {
      title: headline,
      detail: hint || "The deploy stopped at a safe point. Running it again resumes from the last finished step.",
      retryable: true,
      retryLabel: "Resume deploy",
      offerDiagnostics: false,
    };
  }
  return {
    title: headline,
    detail:
      hint ||
      "The server may be partly prepared. Every finished step was recorded, so running deploy again resumes safely once the cause is fixed.",
    retryable: error?.retryable ?? true,
    retryLabel: "Try deploy again",
    offerDiagnostics: true,
  };
}

/**
 * @param {ServerValidateResult} result
 * @returns {Array<{ label: string, ok: boolean, detail: string }>}
 */
export function presentValidationFacts(result) {
  return [
    {
      label: "Reached server",
      ok: Boolean(result.reachable),
      detail: result.reachable ? "The SSH port answered." : "Check the IP address, SSH port, and provider firewall.",
    },
    {
      label: "Logged in",
      ok: Boolean(result.auth_ok),
      detail: result.auth_ok ? "The username and key/password worked." : "Check username, password, or key setup.",
    },
    {
      label: "Sudo works",
      ok: Boolean(result.sudo_ok),
      detail: result.sudo_ok ? "Meridian can make system changes." : "Use root or a user with passwordless sudo.",
    },
    {
      label: "Detected OS",
      ok: Boolean(result.detected_os),
      detail: result.detected_os || "OS was not detected yet.",
    },
  ];
}

/**
 * Invite, commands, and next steps after a finished deploy.
 * @param {DeployResult} result
 * @param {{ afterCancel?: boolean }} [options]
 */
export function presentDeployResult(result, options = {}) {
  const invite = result.connection_page_url || "";
  const testCommand = result.test_command || `meridian test ${result.server_ip}`;
  const steps = [
    "Open the invite link on one device and connect.",
    `Run ${testCommand} after the device connects.`,
    "Add relays or regional route exits only after the first device works.",
  ];
  if (result.domain) {
    steps.push(`If you use Cloudflare for ${result.domain}, keep WebSockets enabled and SSL/TLS on Full (Strict).`);
  }
  return {
    status: options.afterCancel ? "completed after stop request" : "ready",
    tone: /** @type {Tone} */ (options.afterCancel ? "warning" : "ok"),
    title: result.summary || `Deploy completed for ${result.server_ip}.`,
    detail: invite
      ? "The invite link is ready. Test one device before changing route policy or adding relays."
      : "Deploy finished. Run a connection test before adding relays or more devices.",
    lead: invite
      ? "Copy the invite link first, then run a connection test from Terminal."
      : "No invite link was returned. Use the test command and the add-device command.",
    invite,
    testCommand,
    addDeviceCommand: `meridian client add ${result.client_name && result.client_name !== "default" ? "new-device" : "phone"}`,
    steps,
  };
}

/**
 * @param {OperationCancellation | null | undefined} cancellation
 */
export function presentCancelled(cancellation) {
  const view = describeCancellation(cancellation);
  return {
    ...view,
    status: "stopped",
    retryLabel: cancellation?.phase === "queued" ? "Start deploy" : "Resume deploy",
  };
}

/**
 * The progress card: what is happening right now.
 * @param {EngineEvent | null | undefined} latest
 * @param {OperationSnapshot | null | undefined} snapshot
 */
export function presentProgress(latest, snapshot) {
  const event = latest ?? snapshot?.latest_event ?? null;
  /** @type {Record<string, unknown>} */
  const data = event?.data ?? {};
  const step = String(data.step || event?.message || snapshot?.state || "Working");
  const index = Number(data.index || 0);
  const total = Number(data.total || 0);
  const state = snapshot?.state || "running";
  return {
    state,
    title: operationStateLabel(state),
    body: index > 0 && total > 0 ? `${step}, step ${index} of ${total}` : step,
    meta:
      state === "running" || state === "queued"
        ? "This can take a few minutes. Keep this tab open."
        : `${event?.phase || snapshot?.current_phase || "deploy"} · ${event?.type || state}`,
  };
}

/**
 * One timeline row.
 * @param {Partial<EngineEvent>} event
 */
export function presentTimelineEvent(event) {
  return {
    seq: event.seq ?? 0,
    level: event.level ?? "info",
    message: event.message || event.type || "event",
    meta: [event.phase, event.type, event.time].filter(Boolean).join(" · "),
    data: event.data ?? {},
  };
}

/**
 * Impact cards for the review step.
 * @param {{ targetLabel: string, request: DeployRequest, plan: DeployPlan | null, planSource: string }} input
 * @returns {Array<{ title: string, badge: string, detail: string, tone: Tone }>}
 */
export function presentReview({ targetLabel, request, plan, planSource }) {
  const previewed = Boolean(plan);
  const redeploy = plan?.mode === "redeploy";
  const ports = plan?.ports
    ? `Ports: Reality ${plan.ports.reality_port}, WSS ${plan.ports.wss_port}, XHTTP ${plan.ports.xhttp_port}.`
    : "Ports appear after preview.";
  return [
    {
      title: "Target",
      badge: previewed ? "Previewed" : "Needs preview",
      detail: `${targetLabel}. Install target: ${plan?.server_ip || request.ip || "saved server"}.`,
      tone: previewed ? "ok" : "warning",
    },
    {
      title: "VPN services",
      badge: redeploy ? "Update" : "Install",
      detail: `${redeploy ? "Updates existing Meridian services" : "Installs Meridian services"} for ${request.server_name}. ${ports}`,
      tone: "change",
    },
    {
      title: "SSH and firewall",
      badge: request.harden ? "Will harden" : "Hardening off",
      detail: request.harden
        ? "Deploy may tighten SSH and firewall rules after key setup has been verified."
        : "Deploy will avoid SSH hardening for this run. Use this when the VPS already hosts other services.",
      tone: request.harden ? "change" : "quiet",
    },
    {
      title: "First device",
      badge: "Create profile",
      detail: `Creates the first client profile named ${request.client_name}. Test one device before adding relays or route policy.`,
      tone: "ok",
    },
    {
      title: "Traffic rules",
      badge: request.geo_block ? "Block RU" : "Default exit",
      detail: request.geo_block
        ? "Blocks RU destinations on this server. Regional exits are configured separately."
        : "Traffic uses the default exit until a route policy is configured.",
      tone: request.geo_block ? "warning" : "quiet",
    },
    {
      title: "Evidence source",
      badge: previewed ? (planSource === "static" ? "CLI import" : "Engine") : "Missing",
      detail: previewed
        ? "This review is tied to the current deploy fields. Changing any option requires another preview."
        : "Preview changes before deploying so this review is based on real plan evidence.",
      tone: previewed ? "ok" : "warning",
    },
  ];
}
