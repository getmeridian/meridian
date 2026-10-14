// @ts-check
// The frame around every screen: journey rail, step help, status line, mode
// badge, the welcome screen's mode gate, and Engine-only vs static controls.

import { byId, el } from "../dom.js";
import { studioJourneySteps } from "../helpCatalog.js";
import { stepGate, stepProgress, stepStateLabel } from "../journey.js";

/** @typedef {import("../journey.js").GateContext} GateContext */
/** @typedef {import("../engineSession.js").EngineState} EngineState */

/** Copy for each Engine state; static and locked never promise a reconnect. */
export const ENGINE_COPY = {
  ready: {
    badge: "Local Studio",
    gateTitle: "Local Studio is connected",
    gateBody: "Save servers, check SSH, install keys, preview, and deploy from this computer.",
    gateButton: "Open command sheet",
    statusTitle: "Local Studio available",
    statusDetail: "Save servers, install keys, preview, and deploy locally.",
  },
  locked: {
    badge: "Studio key needed",
    gateTitle: "Open the address meridian studio printed",
    gateBody:
      "Studio is running, but this tab has no key for it. Copy the address meridian studio printed in Terminal (it ends with #k=) and open it here. After a restart, the old address stops working.",
    gateButton: "Copy meridian studio",
    statusTitle: "Studio needs its address",
    statusDetail: "Open the address meridian studio printed in Terminal; it ends with #k=.",
  },
  static: {
    badge: "Static Studio",
    gateTitle: "Run meridian studio for the guided path",
    gateBody:
      "This page prepares commands and files. To save servers, check SSH, install keys, and deploy from the browser, run meridian studio in Terminal and open the address it prints.",
    gateButton: "Copy meridian studio",
    statusTitle: "Static command mode",
    statusDetail: "Copy commands and files here, or run meridian studio and open the address it prints.",
  },
};

/** @param {Document} root */
export function createShellView(root) {
  const els = {
    mode: byId(root, "studio-mode"),
    modeLabel: byId(root, "studio-mode-label"),
    draftStatus: byId(root, "studio-draft-status"),
    currentStep: byId(root, "studio-current-step"),
    status: byId(root, "studio-task-status"),
    statusTitle: byId(root, "status-title"),
    statusDetail: byId(root, "status-detail"),
    contextTitle: byId(root, "studio-context-title"),
    contextBody: byId(root, "studio-context-body"),
    contextList: byId(root, "studio-context-list"),
    modeGate: byId(root, "studio-mode-gate"),
    modeGateTitle: byId(root, "mode-gate-title"),
    modeGateBody: byId(root, "mode-gate-body"),
    modeGateCommand: byId(root, "mode-gate-command"),
  };
  const panels = /** @type {HTMLElement[]} */ ([...root.querySelectorAll("[data-step-panel]")]);
  const railButtons = /** @type {HTMLButtonElement[]} */ ([...root.querySelectorAll("[data-step-target]")]);
  const localOnly = /** @type {HTMLElement[]} */ ([...root.querySelectorAll("[data-local-action]")]);
  const staticOnly = /** @type {HTMLElement[]} */ ([...root.querySelectorAll("[data-static-action]")]);

  return {
    els,
    railButtons,

    /** @param {GateContext} ctx */
    renderRail(ctx) {
      for (const button of railButtons) {
        const step = button.dataset.stepTarget ?? "";
        const state = stepProgress(step, ctx);
        const gate = stepGate(step, ctx);
        const meta = studioJourneySteps.find((item) => item.id === step);
        const label = meta ? `${meta.label}: ${meta.short}` : step;
        button.setAttribute("aria-current", step === ctx.journey.currentStep ? "step" : "false");
        button.setAttribute("aria-disabled", state === "locked" ? "true" : "false");
        button.setAttribute("aria-label", state === "locked" ? `${label}: locked. ${gate.reason}` : label);
        button.dataset.state = state;
        button.title = state === "locked" ? gate.reason : "";
        const status = button.querySelector("[data-step-status]");
        if (status) status.textContent = stepStateLabel(state);
      }
    },

    /** Show one screen and its help. @param {string} step */
    showStep(step) {
      for (const panel of panels) panel.hidden = panel.dataset.stepPanel !== step;
      const meta = studioJourneySteps.find((item) => item.id === step) ?? studioJourneySteps[0];
      els.currentStep.textContent = `${meta.label}: ${meta.short}`;
      els.contextTitle.textContent = meta.contextTitle;
      els.contextBody.textContent = meta.contextBody;
      els.contextList.replaceChildren(...meta.contextItems.map((item) => el("li", { text: item })));
    },

    /** Move focus to the new screen's heading for keyboard and screen-reader users. @param {string} step */
    focusStep(step) {
      const heading = panels.find((panel) => panel.dataset.stepPanel === step)?.querySelector("h2");
      if (heading instanceof HTMLElement) {
        heading.setAttribute("tabindex", "-1");
        heading.focus({ preventScroll: true });
      }
    },

    /** @param {string} title @param {string} detail @param {string} [tone] */
    setStatus(title, detail, tone = "ok") {
      els.statusTitle.textContent = title;
      els.statusDetail.textContent = detail;
      els.status.dataset.tone = tone;
    },

    /** @param {EngineState} state */
    renderEngine(state) {
      const ready = state === "ready";
      const copy = ENGINE_COPY[state === "unknown" ? "static" : state];
      els.mode.dataset.state = ready ? "engine" : state;
      els.modeLabel.textContent = state === "unknown" ? "Checking for Studio" : copy.badge;
      els.modeGate.dataset.mode = ready ? "engine" : "static";
      els.modeGateTitle.textContent = copy.gateTitle;
      els.modeGateBody.textContent = copy.gateBody;
      els.modeGateCommand.textContent = copy.gateButton;
      for (const node of localOnly) node.hidden = !ready;
      for (const node of staticOnly) node.hidden = ready;
    },

    /** @param {string} text */
    setDraftStatus(text) {
      els.draftStatus.textContent = text;
    },
  };
}
