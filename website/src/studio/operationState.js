// Pure presentation helpers for local Engine operation snapshots.
// Shapes follow generated/schemas/operation-snapshot, operation-cancel, and
// operation-cancellation; keep wording here so the page script stays thin.

export const TERMINAL_OPERATION_STATES = new Set(["succeeded", "failed", "cancelled", "completed_after_cancel"]);

export function isTerminalOperation(snapshot) {
  return TERMINAL_OPERATION_STATES.has(snapshot?.state);
}

export function operationStateLabel(state) {
  if (state === "queued") return "Waiting to start";
  if (state === "running") return "Deploy in progress";
  if (state === "cancel_requested") return "Stopping after current step";
  if (state === "completed_after_cancel") return "Completed before it could stop";
  if (state === "cancelled") return "Deploy stopped";
  if (state === "failed") return "Needs recovery";
  if (state === "succeeded") return "Deploy complete";
  return state || "Working";
}

// The Cancel control is visible only while an operation can still be stopped.
// A brand-new operation without a snapshot yet counts as running.
export function cancelControl(snapshot, hasOperation = Boolean(snapshot)) {
  const state = snapshot?.state || (hasOperation ? "running" : "");
  if (state === "queued" || state === "running") {
    return { hidden: false, disabled: false, label: "Cancel deploy" };
  }
  if (state === "cancel_requested") {
    return { hidden: false, disabled: true, label: "Stopping after current step…" };
  }
  return { hidden: true, disabled: true, label: "Cancel deploy" };
}

export function describeCancelOutcome(outcome) {
  if (outcome === "cancelled") {
    return {
      title: "Deploy cancelled",
      detail: "It had not started yet, so nothing changed on the server.",
      tone: "warning",
    };
  }
  if (outcome === "cancel_requested" || outcome === "already_requested") {
    return {
      title: "Stopping deploy",
      detail: "Meridian lets the step that is running now finish, then stops. This can take a minute.",
      tone: "running",
    };
  }
  if (outcome === "already_finished") {
    return {
      title: "Deploy already finished",
      detail: "There was nothing left to cancel.",
      tone: "ok",
    };
  }
  return { title: "Cancel request sent", detail: "Waiting for the Engine to confirm.", tone: "running" };
}

export function describeCancellation(cancellation) {
  const remoteChanged = Boolean(cancellation?.remote_changed);
  return {
    title: cancellation?.phase === "queued" ? "Deploy cancelled before it started" : "Deploy stopped safely",
    detail: cancellation?.message || "The deploy stopped before it finished.",
    serverNote: remoteChanged
      ? "Some changes were already applied. Every finished step was recorded, so resuming picks up where it stopped."
      : "Nothing changed on the server.",
    hint: cancellation?.resume_hint || "Run the same deploy again when you are ready.",
    remoteChanged,
  };
}
