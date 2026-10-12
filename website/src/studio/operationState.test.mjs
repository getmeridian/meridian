import assert from "node:assert/strict";
import test from "node:test";

import {
  cancelControl,
  describeCancellation,
  describeCancelOutcome,
  isTerminalOperation,
  operationStateLabel,
} from "./operationState.js";

test("cancel control is offered only while the operation can stop", () => {
  assert.deepEqual(cancelControl({ state: "running" }), { hidden: false, disabled: false, label: "Cancel deploy" });
  assert.equal(cancelControl({ state: "queued" }).disabled, false);
  assert.equal(cancelControl(null, true).hidden, false);
  assert.equal(cancelControl(null).hidden, true);
  for (const state of ["succeeded", "failed", "cancelled", "completed_after_cancel"]) {
    assert.equal(cancelControl({ state }).hidden, true, state);
  }
});

test("a pending cancel disables the control instead of hiding it", () => {
  const control = cancelControl({ state: "cancel_requested" });

  assert.equal(control.hidden, false);
  assert.equal(control.disabled, true);
  assert.match(control.label, /Stopping/);
});

test("cancel outcomes explain no-ops and safe stops", () => {
  assert.equal(describeCancelOutcome("already_finished").tone, "ok");
  assert.match(describeCancelOutcome("cancelled").detail, /nothing changed/);
  assert.equal(describeCancelOutcome("cancel_requested").tone, "running");
  assert.equal(describeCancelOutcome("already_requested").title, describeCancelOutcome("cancel_requested").title);
});

test("cancellation records say whether the server changed and how to resume", () => {
  const partial = describeCancellation({
    phase: "apply",
    message: "Apply stopped after 4 of 12 resource actions.",
    resume_hint: "Run the same deploy again; finished steps are observed and skipped.",
    remote_changed: true,
  });
  const queued = describeCancellation({ phase: "queued", message: "Deploy was cancelled before it started." });

  assert.equal(partial.title, "Deploy stopped safely");
  assert.equal(partial.remoteChanged, true);
  assert.match(partial.serverNote, /already applied/);
  assert.match(partial.hint, /finished steps/);
  assert.equal(queued.title, "Deploy cancelled before it started");
  assert.equal(queued.serverNote, "Nothing changed on the server.");
});

test("terminal states and labels cover cancellation", () => {
  assert.equal(isTerminalOperation({ state: "cancelled" }), true);
  assert.equal(isTerminalOperation({ state: "cancel_requested" }), false);
  assert.equal(operationStateLabel("cancel_requested"), "Stopping after current step");
  assert.equal(operationStateLabel("cancelled"), "Deploy stopped");
});
