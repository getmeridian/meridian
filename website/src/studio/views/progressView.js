// @ts-check
// The Progress screen: progress card, append-only event timeline, result,
// next-step actions, recovery, and the Cancel control.

import { byId, el, renderResult } from "../dom.js";
import { cancelControl } from "../operationState.js";
import { formatJson } from "../staticAdapter.js";
import { presentProgress, presentTimelineEvent } from "../payloadPresenters.js";

/** @typedef {import("../payloadPresenters.js").FailureView} FailureView */
/** @typedef {ReturnType<typeof import("../payloadPresenters.js").presentDeployResult>} DeployResultView */
/** @typedef {import("../generated/schemas/event.schema").Event} EngineEvent */
/** @typedef {import("../generated/schemas/operation-snapshot.schema").OperationSnapshot} OperationSnapshot */

/** @param {Document} root */
export function createProgressView(root) {
  const els = {
    completionCopy: byId(root, "completion-copy"),
    progressCard: byId(root, "operation-progress-card"),
    result: byId(root, "studio-result"),
    actionCenter: byId(root, "deploy-action-center"),
    actionCopy: byId(root, "deploy-action-copy"),
    actionList: byId(root, "deploy-action-list"),
    copyInviteLink: /** @type {HTMLButtonElement} */ (byId(root, "copy-invite-link")),
    copyTestCommand: byId(root, "copy-test-command"),
    copyAddDeviceCommand: byId(root, "copy-add-device-command"),
    recoveryCard: byId(root, "deploy-recovery-card"),
    recoveryTitle: byId(root, "recovery-title"),
    recoveryCopy: byId(root, "recovery-copy"),
    retryDeploy: byId(root, "retry-deploy"),
    copyDiagnostics: byId(root, "copy-diagnostics"),
    timeline: byId(root, "studio-timeline"),
    cancel: /** @type {HTMLButtonElement} */ (byId(root, "cancel-operation")),
  };
  /** @type {Set<number>} */
  let shownSeqs = new Set();
  /** @type {EngineEvent | null} */
  let latestEvent = null;

  return {
    els,

    /** A new operation starts with an empty timeline. @param {string} copy */
    reset(copy) {
      shownSeqs = new Set();
      latestEvent = null;
      els.timeline.replaceChildren();
      els.progressCard.hidden = true;
      els.actionCenter.hidden = true;
      els.recoveryCard.hidden = true;
      els.completionCopy.textContent = copy;
    },

    /**
     * Append events not shown yet. Existing rows (and any expanded Details)
     * are never rebuilt.
     * @param {Array<Partial<EngineEvent>>} events
     */
    appendEvents(events) {
      const rows = [];
      for (const event of events) {
        const seq = Number(event.seq ?? 0);
        if (seq > 0 && shownSeqs.has(seq)) continue;
        if (seq > 0) shownSeqs.add(seq);
        latestEvent = /** @type {EngineEvent} */ (event);
        rows.push(timelineRow(event));
      }
      if (rows.length > 0) els.timeline.append(...rows);
    },

    /** @param {OperationSnapshot | null} snapshot */
    renderProgress(snapshot) {
      if (!latestEvent && !snapshot) {
        els.progressCard.hidden = true;
        return;
      }
      const view = presentProgress(latestEvent, snapshot);
      els.progressCard.dataset.status = view.state;
      els.progressCard.replaceChildren(
        el("strong", { text: view.title }),
        el("span", { text: view.body }),
        el("small", { text: view.meta }),
      );
      els.progressCard.hidden = false;
    },

    /**
     * @param {OperationSnapshot | null} snapshot
     * @param {{ visible: boolean }} options
     */
    renderCancel(snapshot, { visible }) {
      const control = cancelControl(snapshot, visible);
      els.cancel.hidden = !visible || control.hidden;
      els.cancel.disabled = control.disabled;
      els.cancel.textContent = control.label;
    },

    /** @param {{ status: string, title: string, detail: string, tone?: string }} view */
    renderResult(view) {
      renderResult(els.result, view);
    },

    /** @param {DeployResultView} view */
    renderSuccess(view) {
      renderResult(els.result, view);
      els.actionCopy.textContent = view.lead;
      els.actionList.replaceChildren(...view.steps.map((step) => el("li", { text: step })));
      els.copyInviteLink.disabled = !view.invite;
      els.copyInviteLink.dataset.copyValue = view.invite;
      els.copyTestCommand.dataset.copyValue = view.testCommand;
      els.copyAddDeviceCommand.dataset.copyValue = view.addDeviceCommand;
      els.actionCenter.hidden = false;
      els.recoveryCard.hidden = true;
    },

    /**
     * @param {{ title: string, detail: string, retryable: boolean, retryLabel: string, offerDiagnostics: boolean }} view
     */
    renderRecovery(view) {
      els.recoveryTitle.textContent = view.title;
      els.recoveryCopy.textContent = view.detail;
      els.retryDeploy.textContent = view.retryLabel;
      els.retryDeploy.hidden = !view.retryable;
      els.copyDiagnostics.hidden = !view.offerDiagnostics;
      els.recoveryCard.hidden = false;
      els.actionCenter.hidden = true;
    },
  };
}

/** @param {Partial<EngineEvent>} event */
function timelineRow(event) {
  const view = presentTimelineEvent(event);
  return el("li", { className: `studio__timeline-item studio__timeline-item--${view.level}` }, [
    el("span", { text: String(view.seq || "") }),
    el("div", {}, [
      el("strong", { text: view.message }),
      el("small", { text: view.meta }),
      el("details", {}, [el("summary", { text: "Details" }), el("pre", { text: formatJson(view.data) })]),
    ]),
  ]);
}
