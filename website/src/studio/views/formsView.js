// @ts-check
// Form screens (server, connect, keys, deploy). Inputs carry data-field="group.key";
// typing only reports edits, values are written back only when state changes
// from elsewhere (reset, saved server, pasted SSH command).

import { byId, el, renderErrors } from "../dom.js";

/** @typedef {import("../storageAdapter.js").JourneyState} JourneyState */
/** @typedef {import("../staticAdapter.js").FieldError} FieldError */
/** @typedef {import("../generated/schemas/server-profile.schema").ServerProfile} ServerProfile */
/** @typedef {{ group: "server" | "deploy", key: string, value: string | boolean, committed: boolean }} FieldEdit */

/** @param {Document} root */
export function createFormsView(root) {
  const inputs = /** @type {Array<HTMLInputElement | HTMLSelectElement>} */ ([...root.querySelectorAll("[data-field]")]);
  const els = {
    sshCommand: /** @type {HTMLInputElement} */ (byId(root, "server-ssh-command")),
    sshChips: byId(root, "ssh-command-preview"),
    serverErrors: byId(root, "server-errors"),
    deployErrors: byId(root, "deploy-errors"),
    serverActionStatus: byId(root, "server-action-status"),
    keyActionStatus: byId(root, "key-action-status"),
    deployPreviewStatus: byId(root, "deploy-preview-status"),
    validationFacts: byId(root, "validation-facts"),
    savedServerNotice: byId(root, "saved-server-notice"),
    savedServerStart: byId(root, "saved-server-start"),
    savedServerStartList: byId(root, "saved-server-start-list"),
    savedServerField: byId(root, "saved-server-field"),
    savedServer: /** @type {HTMLSelectElement} */ (byId(root, "saved-server")),
    advanced: /** @type {HTMLDetailsElement} */ (byId(root, "deploy-advanced")),
    password: /** @type {HTMLInputElement} */ (byId(root, "server-password")),
    disablePasswordAuth: /** @type {HTMLInputElement} */ (byId(root, "disable-password-auth")),
  };

  /** @param {HTMLInputElement | HTMLSelectElement} input */
  function readInput(input) {
    return input instanceof HTMLInputElement && input.type === "checkbox" ? input.checked : input.value;
  }

  return {
    els,

    /** Write state into every bound input. @param {JourneyState} journey */
    fill(journey) {
      for (const input of inputs) {
        const [group, key] = String(input.dataset.field).split(".");
        /** @type {Record<string, unknown>} */
        const values = group === "server" ? journey.server : journey.deploy;
        const value = values[key];
        if (input instanceof HTMLInputElement && input.type === "checkbox") {
          input.checked = Boolean(value);
        } else {
          input.value = String(value ?? "");
        }
      }
      els.advanced.open = journey.advancedOpen;
    },

    /**
     * `input` events report uncommitted edits (state only); `change` (blur, or a
     * checkbox/select toggle) reports a committed one.
     * @param {(edit: FieldEdit) => void} handler
     */
    onEdit(handler) {
      for (const input of inputs) {
        const [group, key] = String(input.dataset.field).split(".");
        const report = (/** @type {boolean} */ committed) =>
          handler({ group: group === "server" ? "server" : "deploy", key, value: readInput(input), committed });
        input.addEventListener("input", () => report(false));
        input.addEventListener("change", () => report(true));
      }
    },

    /**
     * Show errors only for fields the user has left, or all of them after a submit.
     * @param {"server" | "deploy"} group
     * @param {FieldError[]} errors
     * @param {{ touched: Set<string>, submitted: boolean }} visibility
     */
    renderErrors(group, errors, { touched, submitted }) {
      const shown = submitted ? errors : errors.filter((error) => touched.has(error.field));
      const invalid = new Set(shown.map((error) => error.field));
      for (const input of inputs) {
        const [inputGroup, key] = String(input.dataset.field).split(".");
        if (inputGroup !== group) continue;
        if (invalid.has(key)) input.setAttribute("aria-invalid", "true");
        else input.removeAttribute("aria-invalid");
      }
      renderErrors(group === "server" ? els.serverErrors : els.deployErrors, shown);
    },

    /** @param {{ host: string, ssh_port: number, ssh_user: string, title: string } | null} parsed @param {string} [warning] */
    renderSshChips(parsed, warning = "") {
      if (!els.sshCommand.value.trim()) {
        els.sshChips.hidden = true;
        els.sshChips.replaceChildren();
        return;
      }
      els.sshChips.hidden = false;
      if (!parsed) {
        els.sshChips.replaceChildren(el("span", { text: warning, dataset: { tone: "warning" } }));
        return;
      }
      const chips = [
        ["User", parsed.ssh_user],
        ["IP", parsed.host],
        ["Port", String(parsed.ssh_port)],
        ["Title", parsed.title],
      ];
      els.sshChips.replaceChildren(...chips.map(([label, value]) => el("span", { text: `${label}: ${value}` })));
    },

    /** @param {Array<{ label: string, ok: boolean, detail: string }> | null} facts */
    renderValidationFacts(facts) {
      els.validationFacts.hidden = !facts;
      els.validationFacts.replaceChildren(
        ...(facts ?? []).map((fact) =>
          el("div", { className: fact.ok ? "studio__fact studio__fact--ok" : "studio__fact studio__fact--warn" }, [
            el("strong", { text: fact.label }),
            el("span", { text: fact.detail }),
          ]),
        ),
      );
    },

    /**
     * @param {ServerProfile[]} servers
     * @param {string} selectedId
     */
    renderSavedServers(servers, selectedId) {
      els.savedServerField.hidden = servers.length === 0;
      els.savedServer.replaceChildren(
        new Option("Use the server entered earlier", ""),
        ...servers.map((server) => new Option(`${server.title} (${server.host})`, server.id)),
      );
      els.savedServer.value = servers.some((server) => server.id === selectedId) ? selectedId : "";
      const selected = servers.find((server) => server.id === selectedId);
      els.savedServerNotice.hidden = !selected;
      els.savedServerNotice.textContent = selected
        ? `Using saved server ${selected.title} at ${selected.host}:${selected.ssh_port}.`
        : "";
      const usable = servers.filter((server) => server.auth_state === "validated" || server.auth_state === "key_ready");
      els.savedServerStart.hidden = usable.length === 0;
      els.savedServerStartList.replaceChildren(
        ...usable.map((server) =>
          el("button", { className: "studio__saved-choice", attrs: { type: "button" }, dataset: { useServer: server.id } }, [
            el("strong", { text: server.title }),
            el("span", {
              text: `${server.auth_state === "key_ready" ? "Key ready" : "SSH checked"} · ${server.host}:${server.ssh_port}`,
            }),
          ]),
        ),
      );
    },
  };
}
