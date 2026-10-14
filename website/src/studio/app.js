// @ts-check
// Studio controller: owns the in-memory state, wires DOM events to pure
// journey/adapter functions, and tells views what to render. Typing only
// updates state; screens render on step changes, blur, and Engine answers.

import { byId, showStatus } from "./dom.js";
import { createEngineSession, operationPath, pollOperation } from "./engineSession.js";
import {
  canStartDeploy,
  editDeploy,
  editServer,
  isPreviewCurrent,
  recordPayload,
  requestSignature,
  selectSavedServer,
  stepGate,
  STEP_IDS,
} from "./journey.js";
import { describeCancelOutcome, operationStateLabel } from "./operationState.js";
import {
  presentCancelled,
  presentDeployResult,
  presentFailure,
  presentPayload,
  presentReview,
  presentValidationFacts,
} from "./payloadPresenters.js";
import {
  buildCommandPack,
  buildDeployRequestFromJourney,
  buildServerCliCommands,
  buildServerConnectionDraft,
  formatJson,
  parseEnvelope,
  parseEventStream,
  parseSshCommand,
  validateDeployRequest,
  validateServerConnectionDraft,
} from "./staticAdapter.js";
import { clearJourneyDraft, loadJourneyDraft, saveJourneyDraft } from "./storageAdapter.js";
import { createCommandSheetView } from "./views/commandSheetView.js";
import { createFormsView } from "./views/formsView.js";
import { createProgressView } from "./views/progressView.js";
import { createReviewView } from "./views/reviewView.js";
import { ENGINE_COPY, createShellView } from "./views/shellView.js";

/** @typedef {import("./storageAdapter.js").JourneyState} JourneyState */
/** @typedef {import("./journey.js").Preview} Preview */
/** @typedef {import("./journey.js").GateContext} GateContext */
/** @typedef {import("./generated/schemas/server-profile.schema").ServerProfile} ServerProfile */
/** @typedef {import("./generated/schemas/operation-snapshot.schema").OperationSnapshot} OperationSnapshot */

/** @param {Document} root */
export function startStudio(root) {
  const storage = safeLocalStorage();
  const session = createEngineSession();
  const shell = createShellView(root);
  const forms = createFormsView(root);
  const review = createReviewView(root);
  const progress = createProgressView(root);
  const sheet = createCommandSheetView(root);
  const button = (/** @type {string} */ id) => /** @type {HTMLButtonElement} */ (byId(root, id));

  let journey = loadJourneyDraft(storage);
  /** @type {Preview | null} */
  let preview = null;
  /** @type {ServerProfile[]} */
  let savedServers = [];
  const touched = { server: new Set(), deploy: new Set() };
  const submitted = { server: false, deploy: false };
  /** @type {OperationSnapshot | null} */
  let snapshot = null;
  let operationId = journey.activeOperationId;
  let finishedOperationId = "";
  /** @type {{ stop: () => void } | null} */
  let poller = null;

  // --- derived values -----------------------------------------------------

  const serverDraft = () => buildServerConnectionDraft(journey.server);
  const deployRequest = (confirm = false) => buildDeployRequestFromJourney({ ...journey, confirmDeploy: confirm });
  const serverErrors = () => validateServerConnectionDraft(serverDraft());
  const deployErrors = () => validateDeployRequest(deployRequest());
  const engineReady = () => session.state === "ready";

  /** @returns {GateContext} */
  function gateContext() {
    return {
      journey,
      serverValid: serverErrors().length === 0,
      deployValid: deployErrors().length === 0,
      preview,
      requestSignature: requestSignature(deployRequest()),
      hasOperation: Boolean(operationId || finishedOperationId),
    };
  }

  function persist() {
    saveJourneyDraft(storage, journey);
    shell.setDraftStatus(storage ? "Draft saved locally" : "Draft not saved (storage unavailable)");
  }

  // --- navigation ---------------------------------------------------------

  /** @param {string} step */
  function goTo(step) {
    const gate = stepGate(step, gateContext());
    if (!gate.allowed) {
      shell.setStatus("Finish this step first", gate.reason, "warning");
      shell.renderRail(gateContext());
      return false;
    }
    journey = { ...journey, currentStep: step };
    persist();
    showCurrentStep();
    requestAnimationFrame(() => shell.focusStep(step));
    return true;
  }

  function showCurrentStep() {
    shell.showStep(journey.currentStep);
    shell.renderRail(gateContext());
    if (journey.currentStep === "deploy") {
      forms.renderSavedServers(savedServers, journey.selectedServerId);
    }
    if (journey.currentStep === "review") renderReview();
  }

  // A restored step may need evidence this tab no longer has: previews are never
  // persisted, and only a running operation survives a reload.
  function restoreStep() {
    const ctx = gateContext();
    const needsPreview = journey.currentStep === "review" && !stepGate("review", ctx).allowed;
    const needsOperation = journey.currentStep === "complete" && !ctx.hasOperation;
    if (!STEP_IDS.includes(journey.currentStep) || needsPreview || needsOperation) {
      journey = { ...journey, currentStep: STEP_IDS.includes(journey.currentStep) ? "deploy" : "welcome" };
    }
  }

  function renderReview() {
    const request = deployRequest();
    const current = isPreviewCurrent(preview, requestSignature(request));
    const selected = savedServers.find((server) => server.id === journey.selectedServerId);
    const draft = serverDraft();
    review.renderCards(
      presentReview({
        targetLabel: selected ? `${selected.title} (${selected.host}:${selected.ssh_port})` : `${draft.title} (${draft.host}:${draft.ssh_port})`,
        request,
        plan: current && preview ? preview.plan : null,
        planSource: preview?.source ?? "",
      }),
    );
    review.renderGate(
      current && preview
        ? {
            title: "Preview ready",
            detail: `${preview.source === "static" ? "Imported CLI dry-run" : "Local Engine dry-run"} is loaded for this review. ${
              engineReady() ? "Confirm the changes to unlock deploy." : "Copy the deploy command to run it from Terminal."
            }`,
            tone: "ok",
          }
        : { title: "Preview needs refresh", detail: "Deploy options changed. Preview changes again before deploying.", tone: "warning" },
    );
    updateStartButton();
  }

  function updateStartButton() {
    const request = deployRequest();
    review.setStartEnabled(
      canStartDeploy({
        engineReady: engineReady(),
        deployValid: deployErrors().length === 0,
        confirmed: review.els.confirm.checked,
        preview,
        requestSignature: requestSignature(request),
      }),
    );
  }

  // --- form edits ---------------------------------------------------------

  forms.onEdit((edit) => {
    const patch = /** @type {Record<string, any>} */ ({ [edit.key]: edit.group === "server" ? String(edit.value) : edit.value });
    journey = edit.group === "server" ? editServer(journey, patch) : editDeploy(journey, patch);
    persist();
    if (!edit.committed) return;
    touched[edit.group].add(edit.key);
    renderFieldErrors(edit.group);
    shell.renderRail(gateContext());
    if (edit.group === "deploy" && preview && !isPreviewCurrent(preview, requestSignature(deployRequest()))) {
      showStatus(forms.els.deployPreviewStatus, {
        title: "Preview needs refresh",
        detail: "Deploy options changed since the last preview. Preview changes again before review.",
        tone: "warning",
      });
    }
  });

  /** @param {"server" | "deploy"} group */
  function renderFieldErrors(group) {
    const errors = group === "server" ? serverErrors() : deployErrors();
    forms.renderErrors(group, errors, { touched: touched[group], submitted: submitted[group] });
    return errors;
  }

  /** Mark a form submitted and report whether it is valid. @param {"server" | "deploy"} group */
  function submitForm(group) {
    submitted[group] = true;
    const errors = renderFieldErrors(group);
    if (errors.length > 0) {
      shell.setStatus("Check the highlighted fields", errors[0].message, "warning");
      return false;
    }
    return true;
  }

  forms.els.advanced.addEventListener("toggle", () => {
    journey = { ...journey, advancedOpen: forms.els.advanced.open };
    persist();
  });

  forms.els.savedServer.addEventListener("change", () => {
    const server = savedServers.find((item) => item.id === forms.els.savedServer.value);
    journey = server ? selectSavedServer(journey, server) : { ...journey, selectedServerId: "" };
    persist();
    forms.renderSavedServers(savedServers, journey.selectedServerId);
    shell.renderRail(gateContext());
  });

  forms.els.savedServerStartList.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-use-server]") : null;
    const server = savedServers.find((item) => item.id === (target instanceof HTMLElement ? target.dataset.useServer : ""));
    if (!server) return;
    journey = selectSavedServer(journey, server);
    persist();
    forms.fill(journey);
    showStatus(forms.els.serverActionStatus, { title: "Saved server selected", detail: `${server.title} will be used for this deploy.`, tone: "ok" });
    goTo(journey.readiness.sshValidated ? "deploy" : "connect");
  });

  // Pasting an SSH command fills the connection fields once; typing only previews it.
  forms.els.sshCommand.addEventListener("input", () => {
    const text = forms.els.sshCommand.value.trim();
    try {
      forms.renderSshChips(text ? parseSshCommand(text) : null);
    } catch {
      forms.renderSshChips(null, "Waiting for a full SSH command");
    }
  });
  forms.els.sshCommand.addEventListener("paste", () => setTimeout(importSshCommand, 0));
  forms.els.sshCommand.addEventListener("change", importSshCommand);

  function importSshCommand() {
    const text = forms.els.sshCommand.value.trim();
    if (!text) return;
    try {
      const parsed = parseSshCommand(text);
      journey = editServer(journey, {
        host: parsed.host,
        ssh_port: String(parsed.ssh_port),
        ssh_user: parsed.ssh_user,
        ...(journey.server.title ? {} : { title: parsed.title }),
      });
      persist();
      forms.fill(journey);
      forms.renderSshChips(parsed);
      showStatus(forms.els.serverActionStatus, { title: "SSH command imported", detail: "Server fields were filled from the pasted command.", tone: "ok" });
    } catch (error) {
      showStatus(forms.els.serverActionStatus, {
        title: "Could not read SSH command",
        detail: error instanceof Error ? error.message : "Paste a command like ssh ubuntu@198.51.100.10 -p 2222.",
        tone: "warning",
      });
    }
  }

  // --- Engine actions -----------------------------------------------------

  /**
   * Run one Engine call with a busy button, fold the answer into the journey,
   * and show its presentation.
   * @param {HTMLButtonElement} trigger
   * @param {HTMLElement} statusBox
   * @param {() => Promise<any>} call
   * @param {{ next?: string, when?: keyof JourneyState["readiness"] }} [then]
   */
  async function runAction(trigger, statusBox, call, then = {}) {
    if (trigger.disabled) return null;
    trigger.disabled = true;
    shell.setStatus(`${trigger.textContent?.trim() || "Action"}…`, "Please wait. Keep this page open while Studio works.", "running");
    try {
      const payload = await call();
      journey = recordPayload(journey, payload);
      persist();
      const view = presentPayload(payload);
      showStatus(statusBox, view);
      shell.setStatus(view.title, view.detail, view.tone);
      if (payload?.schema === "meridian.server-validate/v1") {
        forms.renderValidationFacts(presentValidationFacts(payload.result));
      }
      await refreshSavedServers();
      if (then.next && (!then.when || journey.readiness[then.when])) goTo(then.next);
      else shell.renderRail(gateContext());
      return payload;
    } catch (error) {
      showFailure(error, statusBox);
      return null;
    } finally {
      trigger.disabled = false;
    }
  }

  /**
   * @param {unknown} error
   * @param {HTMLElement} [statusBox]
   */
  function showFailure(error, statusBox) {
    const view = presentFailure(/** @type {any} */ (error));
    shell.setStatus("Action failed", view.title, "error");
    if (statusBox) showStatus(statusBox, { title: view.title, detail: view.detail, tone: "error" });
  }

  async function refreshSavedServers() {
    if (!engineReady()) return;
    try {
      const payload = await session.get("/api/v1/servers");
      savedServers = Array.isArray(payload.servers) ? payload.servers : [];
    } catch {
      savedServers = [];
    }
    if (journey.selectedServerId && !savedServers.some((server) => server.id === journey.selectedServerId)) {
      journey = { ...journey, selectedServerId: "" };
      persist();
    }
    forms.renderSavedServers(savedServers, journey.selectedServerId);
  }

  async function saveServerProfile() {
    return session.post("/api/v1/servers", serverDraft());
  }

  button("save-server").addEventListener("click", () => {
    if (!submitForm("server")) return;
    runAction(button("save-server"), forms.els.serverActionStatus, saveServerProfile);
  });

  button("save-and-validate").addEventListener("click", () => {
    if (!submitForm("server")) return;
    if (!engineReady()) {
      goTo("connect");
      return;
    }
    runAction(
      button("save-and-validate"),
      forms.els.serverActionStatus,
      async () => {
        const saved = await saveServerProfile();
        journey = recordPayload(journey, saved);
        return session.post("/api/v1/servers/validate", { server_ref: saved.server?.id || journey.selectedServerId });
      },
      { next: "keys", when: "sshValidated" },
    );
  });

  button("validate-server").addEventListener("click", () => {
    runAction(
      button("validate-server"),
      forms.els.serverActionStatus,
      () =>
        session.post(
          "/api/v1/servers/validate",
          journey.selectedServerId ? { server_ref: journey.selectedServerId } : { draft: serverDraft() },
        ),
      { next: "keys", when: "sshValidated" },
    );
  });

  button("bootstrap-key").addEventListener("click", async () => {
    try {
      await runAction(
        button("bootstrap-key"),
        forms.els.keyActionStatus,
        async () => {
          let serverRef = journey.selectedServerId;
          if (!serverRef) {
            const saved = await saveServerProfile();
            journey = recordPayload(journey, saved);
            serverRef = saved.server?.id || "";
          }
          // The password rides this one request and is never stored anywhere.
          return session.post("/api/v1/servers/bootstrap-key", {
            disable_password_auth: forms.els.disablePasswordAuth.checked,
            key_policy: "generate_meridian",
            password: forms.els.password.value,
            server_ref: serverRef,
          });
        },
        { next: "deploy", when: "keyReady" },
      );
    } finally {
      forms.els.password.value = "";
    }
  });

  button("run-dry-run").addEventListener("click", async () => {
    if (!submitForm("deploy")) return;
    const request = deployRequest();
    const payload = await runAction(button("run-dry-run"), forms.els.deployPreviewStatus, () =>
      session.post("/api/v1/deploy/dry-run", request),
    );
    if (payload?.plan) {
      preview = { plan: payload.plan, source: "engine", signature: requestSignature(request) };
      review.resetConfirmation();
      goTo("review");
    }
  });

  review.els.confirm.addEventListener("change", updateStartButton);

  async function startDeploy() {
    if (!engineReady()) return;
    const start = button("start-deploy");
    start.disabled = true;
    try {
      const payload = await session.post("/api/v1/deploy/start", deployRequest(true));
      beginFollowing(payload.operation, "Deploy started. Progress appears below as each step runs.");
    } catch (error) {
      const active = /** @type {any} */ (error)?.operation;
      if (active?.id) {
        beginFollowing(active, "A deploy for this server is already running. Studio is following it.");
        return;
      }
      showFailure(error, forms.els.deployPreviewStatus);
      progress.renderRecovery(presentFailure(/** @type {any} */ (error)));
      updateStartButton();
    }
  }

  /**
   * @param {OperationSnapshot} operation
   * @param {string} copy
   */
  function beginFollowing(operation, copy) {
    operationId = operation.id;
    snapshot = operation;
    journey = { ...journey, activeOperationId: operationId };
    persist();
    progress.reset(copy);
    goTo("complete");
    follow();
  }

  function follow() {
    poller?.stop();
    const id = operationId;
    if (!id) return;
    progress.renderCancel(snapshot, { visible: true });
    poller = pollOperation(session, id, {
      onSnapshot(next) {
        snapshot = next;
        progress.renderProgress(next);
        progress.renderCancel(next, { visible: true });
        if (next.state === "cancel_requested") {
          const stopping = describeCancelOutcome("cancel_requested");
          shell.setStatus(stopping.title, stopping.detail, "warning");
        } else {
          shell.setStatus(operationStateLabel(next.state), "Progress below updates automatically.", "running");
        }
      },
      onEvents(events) {
        progress.appendEvents(events);
        progress.renderProgress(snapshot);
      },
      onRetrying({ failures }) {
        shell.setStatus("Reconnecting to Studio", `Studio lost contact with its Engine and is retrying (attempt ${failures}).`, "warning");
      },
      onFinished(payload) {
        finishOperation(id, payload);
      },
      onError(error) {
        endOperation(id);
        showFailure(error, forms.els.deployPreviewStatus);
        progress.renderRecovery(presentFailure(/** @type {any} */ (error)));
      },
    });
  }

  /**
   * @param {string} id
   * @param {any} payload the /result answer
   */
  function finishOperation(id, payload) {
    const final = payload.operation ?? snapshot;
    snapshot = final;
    progress.renderProgress(final);
    endOperation(id);
    const state = final?.state;
    if ((state === "succeeded" || state === "completed_after_cancel") && payload.result?.result) {
      const view = presentDeployResult(payload.result.result, { afterCancel: state === "completed_after_cancel" });
      progress.renderSuccess(view);
      shell.setStatus(view.status === "ready" ? "Deploy complete" : "Deploy completed before it could stop", view.title, view.tone);
    } else if (state === "cancelled") {
      const view = presentCancelled(final?.cancellation);
      progress.renderResult({ status: view.status, title: view.detail, detail: view.serverNote, tone: "warning" });
      progress.renderRecovery({
        title: view.title,
        detail: `${view.serverNote} ${view.hint}`,
        retryable: true,
        retryLabel: view.retryLabel,
        offerDiagnostics: true,
      });
      shell.setStatus(view.title, view.serverNote, "warning");
    } else {
      const failure = presentFailure(payload.error ?? { message: "Deploy failed.", category: "system" });
      progress.renderResult({ status: "needs action", title: failure.title, detail: failure.detail, tone: "error" });
      progress.renderRecovery(failure);
      shell.setStatus("Deploy needs attention", failure.title, "error");
    }
  }

  /** @param {string} id */
  function endOperation(id) {
    poller = null;
    finishedOperationId = id;
    operationId = "";
    journey = { ...journey, activeOperationId: "" };
    persist();
    progress.renderCancel(snapshot, { visible: false });
    shell.renderRail(gateContext());
  }

  button("start-deploy").addEventListener("click", startDeploy);
  button("retry-deploy").addEventListener("click", () => {
    if (engineReady()) startDeploy();
  });

  button("cancel-operation").addEventListener("click", async () => {
    if (!operationId) return;
    const cancel = button("cancel-operation");
    cancel.disabled = true;
    try {
      const payload = await session.cancelOperation(operationId);
      const outcome = describeCancelOutcome(payload.outcome);
      snapshot = payload.operation ?? snapshot;
      shell.setStatus(outcome.title, outcome.detail, outcome.tone);
      progress.renderCancel(snapshot, { visible: Boolean(operationId) });
    } catch (error) {
      progress.renderCancel(snapshot, { visible: Boolean(operationId) });
      showFailure(error);
    }
  });

  button("copy-diagnostics").addEventListener("click", async () => {
    const id = finishedOperationId || operationId;
    if (!engineReady() || !id) return;
    try {
      const payload = await session.get(operationPath(id, "diagnostics"));
      await navigator.clipboard.writeText(payload.markdown || formatJson(payload));
      shell.setStatus("Diagnostics copied", "The report is redacted and ready to paste into a support conversation.", "ok");
    } catch (error) {
      showFailure(error);
    }
  });

  // --- static fallback ------------------------------------------------------

  /** @param {string} text @param {string} label */
  async function copy(text, label) {
    try {
      await navigator.clipboard.writeText(text);
      sheet.setCopyStatus(`${label} copied`);
      shell.setStatus(`${label} copied`, "Paste it into Terminal when you are ready.", "ok");
      setTimeout(() => sheet.setCopyStatus(""), 1600);
    } catch {
      shell.setStatus("Copy failed", "Your browser blocked clipboard access. Open Commands and copy the text by hand.", "warning");
    }
  }

  /** @param {string} filename @param {unknown} value */
  function downloadJson(filename, value) {
    const url = URL.createObjectURL(new Blob([`${formatJson(value)}\n`], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  }

  const commandPack = () => buildCommandPack({ deployRequest: deployRequest(), serverDraft: serverDraft() });
  const openSheet = () => sheet.open({ commands: commandPack(), serverDraft: serverDraft(), deployRequest: deployRequest() });

  button("open-command-sheet").addEventListener("click", openSheet);
  button("mode-gate-command").addEventListener("click", () => {
    if (engineReady()) openSheet();
    else copy("meridian studio", "Command");
  });
  button("copy-ssh-command").addEventListener("click", () => copy(buildServerCliCommands(serverDraft()).connect, "SSH command"));
  button("copy-key-command").addEventListener("click", () => copy(buildServerCliCommands(serverDraft()).copyKey, "Key command"));
  button("copy-save-command").addEventListener("click", () => copy(commandPack().saveServer, "Save command"));
  button("copy-dry-run-command").addEventListener("click", () => copy(commandPack().dryRun, "Preview command"));
  button("copy-sheet-deploy-command").addEventListener("click", () => copy(commandPack().deploy, "Deploy command"));
  button("copy-request-dry-run-command").addEventListener("click", () => copy(commandPack().requestDryRun, "Request-file command"));
  button("copy-static-dry-run").addEventListener("click", () => {
    if (submitForm("deploy")) copy(commandPack().dryRun, "Preview command");
  });
  button("copy-static-deploy").addEventListener("click", () => {
    if (submitForm("deploy")) copy(commandPack().deploy, "Deploy command");
  });
  button("copy-deploy-command").addEventListener("click", () => copy(commandPack().deploy, "Deploy command"));
  button("download-server").addEventListener("click", () => downloadJson("server.json", serverDraft()));
  for (const id of ["download-request", "download-request-sheet"]) {
    button(id).addEventListener("click", () => downloadJson("deploy.json", deployRequest()));
  }
  for (const [id, label] of [
    ["copy-invite-link", "Invite link"],
    ["copy-test-command", "Test command"],
    ["copy-add-device-command", "Add-device command"],
  ]) {
    const node = button(id);
    node.addEventListener("click", () => copy(node.dataset.copyValue || "", label));
  }

  button("confirm-manual-ssh").addEventListener("click", () => {
    journey = { ...journey, readiness: { ...journey.readiness, sshValidated: true } };
    persist();
    showStatus(forms.els.serverActionStatus, { title: "Manual SSH check recorded", detail: "Studio will trust the SSH command you ran in Terminal.", tone: "ok" });
    goTo("keys");
  });
  button("confirm-manual-key").addEventListener("click", () => {
    journey = { ...journey, readiness: { ...journey.readiness, keyReady: true } };
    persist();
    showStatus(forms.els.keyActionStatus, { title: "Manual key setup recorded", detail: "Studio will continue with deploy preparation.", tone: "ok" });
    goTo("deploy");
  });

  button("parse-output").addEventListener("click", () => {
    const text = sheet.els.paste.value.trim();
    if (!text) {
      sheet.setParseStatus("No output pasted");
      return;
    }
    try {
      if (!text.startsWith("{")) {
        const events = parseEventStream(text);
        progress.appendEvents(events);
        sheet.setParseStatus(`${events.length} events parsed`);
        return;
      }
      const envelope = parseEnvelope(text);
      if (envelope.command === "deploy" && envelope.data?.mode && envelope.data?.ports) {
        importDryRun(envelope);
        return;
      }
      progress.renderResult(presentPayload(envelope));
      sheet.setParseStatus("Envelope parsed");
    } catch (error) {
      sheet.setParseStatus(error instanceof Error ? error.message : "Could not parse output");
    }
  });

  /** @param {any} envelope a `meridian deploy --dry-run --json` envelope */
  function importDryRun(envelope) {
    const request = deployRequest();
    const selected = savedServers.find((server) => [server.id, server.title, server.host].includes(request.requested_server ?? ""));
    const expectedIp = request.ip || selected?.host || "";
    if (expectedIp && envelope.data.server_ip !== expectedIp) {
      sheet.setParseStatus(`That dry-run is for ${envelope.data.server_ip}, but this draft targets ${expectedIp}.`);
      return;
    }
    preview = { plan: envelope.data, source: "static", signature: requestSignature(request) };
    showStatus(forms.els.deployPreviewStatus, { title: "Preview imported", detail: "The pasted dry-run output is now the review source.", tone: "ok" });
    sheet.setParseStatus("Dry-run preview imported");
    sheet.close();
    goTo("review");
  }

  // --- navigation buttons, reset, Engine state ----------------------------

  for (const node of root.querySelectorAll("[data-next-step], [data-prev-step], [data-step-target]")) {
    if (!(node instanceof HTMLElement)) continue;
    const target = node.dataset.nextStep || node.dataset.prevStep || node.dataset.stepTarget || "";
    node.addEventListener("click", () => goTo(target));
  }

  button("studio-reset").addEventListener("click", () => {
    journey = clearJourneyDraft(storage);
    preview = null;
    touched.server.clear();
    touched.deploy.clear();
    submitted.server = false;
    submitted.deploy = false;
    forms.fill(journey);
    forms.els.password.value = "";
    forms.renderErrors("server", [], { touched: touched.server, submitted: false });
    forms.renderErrors("deploy", [], { touched: touched.deploy, submitted: false });
    forms.renderValidationFacts(null);
    shell.setStatus("Ready", "The draft was cleared.", "ok");
    goTo("welcome");
  });

  session.onChange((state) => {
    shell.renderEngine(state);
    const copyFor = ENGINE_COPY[state === "unknown" ? "static" : state];
    shell.setStatus(copyFor.statusTitle, copyFor.statusDetail, state === "locked" ? "warning" : "ok");
    if (journey.currentStep === "review") renderReview();
    shell.renderRail(gateContext());
  });

  // Opening the new address in this same tab only changes the fragment.
  window.addEventListener("hashchange", () => {
    if (window.location.hash.includes("k=")) {
      session.handshake().then(afterHandshake);
    }
  });

  async function afterHandshake() {
    await refreshSavedServers();
    if (engineReady() && operationId && !poller) {
      progress.reset("Reconnecting to the deploy that was running.");
      shell.setStatus("Resuming operation", "Studio is reconnecting to the active deploy.", "running");
      goTo("complete");
      follow();
    }
  }

  // --- start ----------------------------------------------------------------

  restoreStep();
  forms.fill(journey);
  shell.renderEngine("unknown");
  showCurrentStep();
  persist();
  session.handshake().then(afterHandshake);
}

function safeLocalStorage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}
