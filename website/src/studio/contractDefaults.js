export function workflowDefaults(workflow, overrides = {}) {
  const values = {};
  for (const field of workflow.fields ?? []) {
    values[field.id] = field.id in overrides ? overrides[field.id] : field.default;
  }
  return values;
}

export function workflowOptions(workflow, fieldId) {
  const field = (workflow.fields ?? []).find((item) => item.id === fieldId);
  return field?.options ?? [];
}

export function workflowField(workflow, fieldId) {
  return (workflow.fields ?? []).find((item) => item.id === fieldId) ?? null;
}
