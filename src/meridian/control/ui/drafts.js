/* Unsaved form values stay in memory. Only changed, connected forms guard leaving. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var entries = [], asking = null, focusTurn = 0;

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
    form.addEventListener("focusin", function (event) {
      if (!event.target.matches("input, select, textarea")) return;
      entry.focused = event.target; entry.focusTurn = ++focusTurn;
    });
    // Tabbing through untouched fields on the way out must not move Stay away from the edit.
    form.addEventListener("input", function (event) { entry.edited = event.target; entry.focusTurn = ++focusTurn; });
  };
  function usable(field) { return field && field.isConnected && !field.disabled && !field.closest("[hidden]"); }
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
    // A navigation click has already focused its link. Stay returns to the last edited field.
    var drafts = entries.filter(function (entry) { return (!container || container.contains(entry.form)) && changed(entry); });
    drafts.sort(function (first, second) { return (second.focusTurn || 0) - (first.focusTurn || 0); });
    var field = drafts[0] && [drafts[0].edited, drafts[0].focused]
      .concat(Array.from(drafts[0].form.querySelectorAll("input, select, textarea"))).filter(usable)[0];
    var selection = field && typeof field.selectionStart === "number" ? [field.selectionStart, field.selectionEnd] : null;
    asking = M.askToLeave().then(function (leave) {
      if (!leave && usable(field)) {
        field.focus(); if (selection) field.setSelectionRange(selection[0], selection[1]);
      }
      return leave;
    }).finally(function () { asking = null; });
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
