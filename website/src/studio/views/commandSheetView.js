// @ts-check
// The command sheet: a native <dialog> with copyable commands, request files,
// and the pasted-output parser. Contents are filled only when it opens.

import { byId } from "../dom.js";
import { formatJson } from "../staticAdapter.js";

/** @param {Document} root */
export function createCommandSheetView(root) {
  const dialog = /** @type {HTMLDialogElement} */ (byId(root, "command-sheet"));
  const els = {
    close: byId(root, "close-command-sheet"),
    saveCommand: byId(root, "save-command"),
    dryRunCommand: byId(root, "dry-run-command"),
    deployCommand: byId(root, "deploy-command"),
    requestDryRunCommand: byId(root, "request-dry-run-command"),
    serverJson: byId(root, "server-json"),
    requestJson: byId(root, "request-json"),
    copyStatus: byId(root, "copy-status"),
    paste: /** @type {HTMLTextAreaElement} */ (byId(root, "studio-paste")),
    parseStatus: byId(root, "parse-status"),
  };
  /** @type {HTMLElement | null} */
  let returnFocus = null;

  dialog.addEventListener("close", () => {
    if (returnFocus?.isConnected) returnFocus.focus();
    returnFocus = null;
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  els.close.addEventListener("click", () => dialog.close());

  return {
    els,

    /**
     * @param {{ commands: { saveServer: string, dryRun: string, deploy: string, requestDryRun: string }, serverDraft: unknown, deployRequest: unknown }} contents
     */
    open(contents) {
      els.saveCommand.textContent = contents.commands.saveServer;
      els.dryRunCommand.textContent = contents.commands.dryRun;
      els.deployCommand.textContent = contents.commands.deploy;
      els.requestDryRunCommand.textContent = contents.commands.requestDryRun;
      els.serverJson.textContent = formatJson(contents.serverDraft);
      els.requestJson.textContent = formatJson(contents.deployRequest);
      returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      if (!dialog.open) dialog.showModal();
      requestAnimationFrame(() => els.close.focus());
    },

    close() {
      dialog.close();
    },

    /** @param {string} text */
    setCopyStatus(text) {
      els.copyStatus.textContent = text;
    },

    /** @param {string} text */
    setParseStatus(text) {
      els.parseStatus.textContent = text;
    },
  };
}
