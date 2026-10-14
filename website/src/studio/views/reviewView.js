// @ts-check
// The Review screen: impact cards, the preview gate, confirmation, Start deploy.

import { byId, el, showStatus } from "../dom.js";

/** @typedef {ReturnType<typeof import("../payloadPresenters.js").presentReview>} ReviewCards */

/** @param {Document} root */
export function createReviewView(root) {
  const els = {
    summary: byId(root, "review-summary"),
    gate: byId(root, "plan-gate-status"),
    confirm: /** @type {HTMLInputElement} */ (byId(root, "confirm-deploy")),
    start: /** @type {HTMLButtonElement} */ (byId(root, "start-deploy")),
  };
  return {
    els,

    /** @param {ReviewCards} cards */
    renderCards(cards) {
      els.summary.replaceChildren(
        ...cards.map((card) =>
          el("article", { className: "studio__impact-card", dataset: { tone: card.tone } }, [
            el("div", {}, [el("strong", { text: card.title }), el("span", { text: card.badge })]),
            el("p", { text: card.detail }),
          ]),
        ),
      );
    },

    /** @param {{ title: string, detail: string, tone: string }} status */
    renderGate(status) {
      showStatus(els.gate, status);
    },

    /** @param {boolean} enabled */
    setStartEnabled(enabled) {
      els.start.disabled = !enabled;
    },

    resetConfirmation() {
      els.confirm.checked = false;
    },
  };
}
