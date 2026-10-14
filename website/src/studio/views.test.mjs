import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import test from "node:test";

import { JSDOM } from "jsdom";

import { createFormsView } from "./views/formsView.js";
import { createProgressView } from "./views/progressView.js";

const studioDir = new URL("./", import.meta.url);

const PROGRESS_IDS = [
  "completion-copy",
  "operation-progress-card",
  "studio-result",
  "deploy-action-center",
  "deploy-action-copy",
  "deploy-action-list",
  "copy-invite-link",
  "copy-test-command",
  "copy-add-device-command",
  "deploy-recovery-card",
  "recovery-title",
  "recovery-copy",
  "retry-deploy",
  "copy-diagnostics",
  "cancel-operation",
];

const FORM_IDS = [
  "ssh-command-preview",
  "server-errors",
  "deploy-errors",
  "server-action-status",
  "key-action-status",
  "deploy-preview-status",
  "validation-facts",
  "saved-server-notice",
  "saved-server-start",
  "saved-server-start-list",
  "saved-server-field",
];

/** Install a jsdom window as the globals the views use. */
function page(body) {
  const dom = new JSDOM(`<!doctype html><body>${body}</body>`);
  const previous = {};
  for (const name of ["document", "HTMLInputElement", "Option", "Event"]) {
    previous[name] = globalThis[name];
    globalThis[name] = dom.window[name];
  }
  return {
    document: dom.window.document,
    window: dom.window,
    restore() {
      for (const [name, value] of Object.entries(previous)) globalThis[name] = value;
    },
  };
}

function event(seq, message) {
  return { seq, type: "provision.step.started", phase: "provision", message, data: { step: message } };
}

test("new events append without rebuilding rows a reader has opened", () => {
  const dom = page(`${PROGRESS_IDS.map((id) => `<div id="${id}"></div>`).join("")}<ol id="studio-timeline"></ol>`);
  try {
    const view = createProgressView(dom.document);
    view.reset("Deploying");
    view.appendEvents([event(1, "Install nginx")]);
    const firstRow = dom.document.querySelector("#studio-timeline li");
    firstRow.querySelector("details").open = true;

    view.appendEvents([event(1, "Install nginx"), event(2, "Start Xray")]);

    const rows = dom.document.querySelectorAll("#studio-timeline li");
    assert.equal(rows.length, 2, "an event already shown is not repeated");
    assert.equal(rows[0], firstRow, "the existing row is the same node");
    assert.equal(rows[0].querySelector("details").open, true);
  } finally {
    dom.restore();
  }
});

test("event text is rendered as text, never markup", () => {
  const dom = page(`${PROGRESS_IDS.map((id) => `<div id="${id}"></div>`).join("")}<ol id="studio-timeline"></ol>`);
  try {
    const view = createProgressView(dom.document);
    view.appendEvents([event(1, "<img src=x onerror=alert(1)>")]);

    assert.equal(dom.document.querySelector("#studio-timeline img"), null);
    assert.match(dom.document.querySelector("#studio-timeline strong").textContent, /<img/);
  } finally {
    dom.restore();
  }
});

test("typing reports an uncommitted edit and leaves the input alone", () => {
  const dom = page(`
    ${FORM_IDS.map((id) => `<div id="${id}"></div>`).join("")}
    <input id="server-ssh-command"><select id="saved-server"></select>
    <details id="deploy-advanced"></details>
    <input id="server-password" type="password"><input id="disable-password-auth" type="checkbox">
    <input data-field="server.host"><input data-field="deploy.harden" type="checkbox">`);
  try {
    const view = createFormsView(dom.document);
    const edits = [];
    view.onEdit((edit) => edits.push(edit));
    const host = dom.document.querySelector('[data-field="server.host"]');
    host.focus();

    host.value = "198.51.100.1";
    host.dispatchEvent(new dom.window.Event("input"));
    host.dispatchEvent(new dom.window.Event("change"));

    assert.deepEqual(edits, [
      { group: "server", key: "host", value: "198.51.100.1", committed: false },
      { group: "server", key: "host", value: "198.51.100.1", committed: true },
    ]);
    assert.equal(dom.document.activeElement, host);
  } finally {
    dom.restore();
  }
});

test("field errors stay hidden until the field is left or the form submitted", () => {
  const dom = page(`
    ${FORM_IDS.map((id) => `<div id="${id}"></div>`).join("")}
    <input id="server-ssh-command"><select id="saved-server"></select>
    <details id="deploy-advanced"></details>
    <input id="server-password" type="password"><input id="disable-password-auth" type="checkbox">
    <input data-field="server.host"><input data-field="server.ssh_port">`);
  try {
    const view = createFormsView(dom.document);
    const errors = [
      { field: "host", message: "Enter a public IP." },
      { field: "ssh_port", message: "Use a port from 1 to 65535." },
    ];
    const host = dom.document.querySelector('[data-field="server.host"]');
    const port = dom.document.querySelector('[data-field="server.ssh_port"]');

    view.renderErrors("server", errors, { touched: new Set(["host"]), submitted: false });
    assert.equal(host.getAttribute("aria-invalid"), "true");
    assert.equal(port.hasAttribute("aria-invalid"), false);

    view.renderErrors("server", errors, { touched: new Set(), submitted: true });
    assert.equal(port.getAttribute("aria-invalid"), "true");
  } finally {
    dom.restore();
  }
});

test("Studio scripts never use HTML sinks", async () => {
  const files = [];
  for (const dir of ["", "views/"]) {
    for (const name of await readdir(new URL(dir, studioDir))) {
      if (name.endsWith(".js")) files.push(`${dir}${name}`);
    }
  }
  assert.ok(files.includes("app.js"));
  for (const file of files) {
    const source = await readFile(new URL(file, studioDir), "utf8");
    assert.doesNotMatch(source, /\.(innerHTML|outerHTML)\b|insertAdjacentHTML|document\.write/, file);
  }
});
