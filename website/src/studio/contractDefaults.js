// @ts-check
// Read labels, help, defaults, and options from generated workflow contracts so
// Studio never hand-copies what the CLI wizard already says.

/**
 * @typedef {object} WorkflowField
 * @property {string} id
 * @property {string} label
 * @property {string} help_text
 * @property {unknown} default
 * @property {string} kind
 * @property {boolean} required
 * @property {Array<{ value: string, label: string, description: string }>} options
 */
/** @typedef {{ fields: WorkflowField[] }} Workflow */

/**
 * @param {Workflow} workflow
 * @param {Record<string, unknown>} [overrides]
 * @returns {Record<string, unknown>}
 */
export function workflowDefaults(workflow, overrides = {}) {
  /** @type {Record<string, unknown>} */
  const values = {};
  for (const field of workflow.fields ?? []) {
    values[field.id] = field.id in overrides ? overrides[field.id] : field.default;
  }
  return values;
}

/**
 * @param {Workflow} workflow
 * @param {string} fieldId
 */
export function workflowOptions(workflow, fieldId) {
  return workflowField(workflow, fieldId)?.options ?? [];
}

/**
 * @param {Workflow} workflow
 * @param {string} fieldId
 * @returns {WorkflowField | null}
 */
export function workflowField(workflow, fieldId) {
  return (workflow.fields ?? []).find((item) => item.id === fieldId) ?? null;
}

/**
 * Label, help, and placeholder (the default) for one form field. Studio-only
 * help fills in only where the contract has none.
 * @param {Workflow} workflow
 * @param {string} fieldId
 * @param {Record<string, string>} [fallbackHelp]
 * @returns {{ label: string, help: string, placeholder: string }}
 */
export function contractFieldText(workflow, fieldId, fallbackHelp = {}) {
  const field = workflowField(workflow, fieldId);
  if (!field) {
    throw new Error(`Workflow has no field ${fieldId}`);
  }
  const placeholder = typeof field.default === "string" ? field.default : "";
  return { label: field.label, help: field.help_text || fallbackHelp[fieldId] || "", placeholder };
}

/**
 * Human labels for every field id across workflows, for validation messages.
 * @param {Workflow[]} workflows
 * @returns {Record<string, string>}
 */
export function workflowLabels(...workflows) {
  /** @type {Record<string, string>} */
  const labels = {};
  for (const workflow of workflows) {
    for (const field of workflow.fields ?? []) {
      labels[field.id] ??= field.label;
    }
  }
  return labels;
}
