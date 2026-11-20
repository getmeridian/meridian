/* The browser cannot attest that an encrypted copy was kept off the server. */
(function () {
  "use strict";
  var M = window.M, h = M.h;

  function download() {
    var file = null, magic = "meridian-snapshot/v1\n";
    return M.dialog(M.t("recovery.download"), function (close) {
      var actions = h("div", { class: "actions" });
      var error = h("p", { class: "form-error", role: "alert" });
      var prepare = h("button", { type: "button", class: "btn btn-primary", text: M.t("recovery.prepare"), onclick: function () {
        prepare.disabled = true;
        error.textContent = "";
        M.api("POST", "api/backup/download", undefined, { binary: true }).then(function (blob) {
          return blob.slice(0, magic.length).text().then(function (prefix) {
            if (prefix !== magic) throw new M.ApiError(0, "backup_invalid", "", 0, false);
            if (!actions.isConnected) return;
            file = URL.createObjectURL(blob);
            var link = h("a", { class: "btn btn-primary", download: "backup.snapshot", text: M.t("recovery.save") });
            link.setAttribute("href", file); // File is a local Blob URL, never an address from the service.
            M.mount(actions, [h("button", { type: "button", class: "btn btn-ghost",
              onclick: function () { close(); } }, M.t("action.close")), link]);
            link.focus();
          });
        }).catch(function (failure) {
          prepare.disabled = false;
          if (failure.code !== "cancelled") error.textContent = M.errorText(failure);
        });
      } });
      M.mount(actions, [h("button", { type: "button", class: "btn btn-ghost", onclick: function () { close(); } },
        M.t("action.cancel")), prepare]);
      return [h("p", { text: M.t("recovery.recipients") }), h("p", { class: "muted", text: M.t("recovery.browser_note") }), M.command("meridian backup pull"), error, actions];
    }).finally(function () { if (file) URL.revokeObjectURL(file); });
  }

  M.backupCard = function () {
    var title = h("h2", { text: M.t("recovery.title") });
    var summary = h("p", { "data-backup-summary": "" }), detail = h("p", { class: "muted small" });
    var setup = h("div", { class: "stack" }), action = h("button", { type: "button", class: "btn btn-ghost btn-sm",
      "data-backup-download": "", hidden: true, text: M.t("recovery.download"), onclick: download });
    var card = h("section", { class: "card", "data-backup-status": "" }, title, summary, detail, setup,
      h("div", { class: "row-actions" }, action));
    var setupKey;
    function draw(status) {
      var known = status && Number.isSafeInteger(status.changes);
      var saved = known && Number.isSafeInteger(status.last_saved_changes) && status.last_saved_at;
      var missing = saved ? status.changes - status.last_saved_changes : null;
      summary.textContent = !known ? M.t("recovery.unavailable") : !saved ? M.t("recovery.never") : missing === 0
        ? M.t("recovery.current") : M.t("recovery.pending", { count: M.count(missing) });
      detail.textContent = saved ? M.t("recovery.confirmed_at", { date: M.date(status.last_saved_at, true) }) : "";
      detail.hidden = !saved;
      var state = !known ? "unavailable" : !status.has_recovery_key ? "key" : !status.has_deployer_state ? "deposit" : "ready";
      action.hidden = state !== "ready";
      if (state !== setupKey) {
        setupKey = state;
        M.mount(setup, state === "key" || state === "deposit" ? h("div", {},
          h("p", { class: "muted small", text: state === "key" ? M.t("recovery.key_needed") : M.t("recovery.deposit_needed") }),
          M.command(state === "key" ? "meridian backup recovery-key" : "meridian backup pull")) : null);
      }
    }
    return { card: card, draw: draw };
  };
})();
