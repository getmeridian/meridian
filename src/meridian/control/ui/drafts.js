/* Unsaved form values stay in memory. Only changed, connected person/password forms guard leaving. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var entries = [], asking = null;

  function values(form) {
    return Array.from(form.querySelectorAll("input, select, textarea")).map(function (field) {
      return { field: field,
        value: field.disabled ? null : field.type === "checkbox" || field.type === "radio" ? field.checked : field.value,
        badInput: !field.disabled && !!(field.validity && field.validity.badInput) };
    });
  }
  function changed(entry) {
    var current = values(entry.form);
    return current.length !== entry.initial.length || current.some(function (value, index) {
      return value.field !== entry.initial[index].field || value.value !== entry.initial[index].value
        || value.badInput !== entry.initial[index].badInput;
    });
  }
  function prune() {
    entries = entries.filter(function (entry) {
      if (entry.form.isConnected) return true;
      entry.initial = [];
      delete entry.form.meridianDraft;
      return false;
    });
  }
  M.trackForm = function (form) {
    if (form.meridianDraft) return;
    var entry = { form: form, initial: values(form) };
    form.meridianDraft = entry;
    entries.push(entry);
  };
  M.formDirty = function (form) { return !!(form && form.meridianDraft && changed(form.meridianDraft)); };
  M.hasUnsavedEdits = function (container) {
    prune();
    return entries.some(function (entry) { return (!container || container.contains(entry.form)) && changed(entry); });
  };
  M.discardDrafts = function (container) {
    entries = entries.filter(function (entry) {
      if (container && !container.contains(entry.form)) return true;
      entry.initial = [];
      delete entry.form.meridianDraft;
      return false;
    });
  };
  M.resetDraft = function (form) { if (form.meridianDraft) form.meridianDraft.initial = values(form); };

  M.askToLeave = function () {
    return M.dialog(M.t("draft.title"), function (close) {
      return [M.h("p", { class: "muted", text: M.t("draft.text") }),
        M.h("div", { class: "actions" },
          M.h("button", { class: "btn btn-primary", type: "button", autofocus: true, dataset: { draftChoice: "stay" },
            onclick: function () { close(false); } }, M.t("draft.stay")),
          M.h("button", { class: "btn btn-ghost", type: "button", dataset: { draftChoice: "leave" },
            onclick: function () { close(true); } }, M.t("draft.leave")))];
    }).then(Boolean);
  };
  M.tryLeave = function (container) {
    if (!M.hasUnsavedEdits(container)) return Promise.resolve(true);
    if (asking) return asking;
    asking = M.askToLeave().finally(function () { asking = null; });
    return asking;
  };

  /* Freeze only this saving form; a successful reply resets it, while failures retain its draft. */
  M.saveDraft = function (form, work) {
    if (form.meridianSaving) return Promise.reject(new M.ApiError(0, "cancelled", ""));
    form.meridianSaving = true;
    var submitted = values(form);
    var fields = Array.from(form.querySelectorAll("input, select, textarea"));
    var disabled = fields.map(function (field) { return field.disabled; });
    fields.forEach(function (field) { field.disabled = true; });
    return M.busy(form, work).then(function (result) {
      if (form.meridianDraft) form.meridianDraft.initial = submitted;
      return result;
    }).finally(function () {
      fields.forEach(function (field, index) { field.disabled = disabled[index]; });
      form.meridianSaving = false;
    });
  };

  window.addEventListener("beforeunload", function (event) {
    if (!M.hasUnsavedEdits()) return;
    event.preventDefault();
    event.returnValue = "";
  });
  new MutationObserver(prune).observe(document.body, { childList: true, subtree: true });
})();
