/* Private person policy choices; reviewed definitions remain read-only. */
(function () {
  "use strict";
  var M = (window.M = window.M || {}), h = M.h;

  M.personDeliveryPolicy = function (user, changed) {
    var username = user.username, state = null, ready = false, pending = false, version = 0;
    var endpoint = "api/users/" + encodeURIComponent(username) + "/delivery-policy";
    var select = h("select", { name: "delivery_policy", disabled: true, "data-policy": "override" });
    var effective = h("p", { class: "hint", "data-policy": "effective", "aria-live": "polite" });
    var error = h("p", { class: "form-error", role: "alert" });
    var save = h("button", { type: "submit", class: "btn btn-primary", disabled: true }, M.t("delivery.preview"));
    var form = h("form", { class: "stack", novalidate: true },
      M.field(M.t("delivery.choice"), select, M.t("delivery.private")), effective, error, save);
    var card = h("section", { class: "card" }, h("h2", { text: M.t("delivery.title") }),
      h("p", { class: "muted small", text: M.t("delivery.refresh") }), form);

    function draw() {
      if (!state) return;
      effective.textContent = M.t("delivery.effective", {
        policy: state.effective.policy.id, source: M.t("delivery.source_" + state.effective.source),
      });
    }
    function load() {
      var turn = ++version;
      return Promise.all([M.api("GET", "api/delivery-policies", undefined, { passive: true }),
        M.api("GET", endpoint, undefined, { passive: true })]).then(function (results) {
        if (turn !== version || !card.isConnected) return;
        state = results[1];
        M.mount(select, [h("option", { value: "", text: M.t("delivery.inherit") }), results[0].policies.map(function (policy) {
          return h("option", { value: policy.id, text: policy.id });
        })]);
        select.value = state.override || "";
        select.disabled = save.disabled = false; ready = true;
        M.trackForm(form); draw();
      }, function (failure) {
        error.textContent = M.errorText(failure);
        M.mount(effective, h("button", { type: "button", class: "btn btn-ghost btn-sm", onclick: load }, M.t("action.retry")));
      });
    }
    function refresh() {
      if (!ready || pending || !card.isConnected) return;
      var turn = ++version;
      M.api("GET", endpoint, undefined, { passive: true }).then(function (latest) {
        if (turn !== version || !card.isConnected || pending) return;
        state = latest; draw();
        if (!M.formDirty(form)) { select.value = state.override || ""; M.resetDraft(form); }
      }, function (failure) { if (card.isConnected) error.textContent = M.errorText(failure); });
    }
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (!ready || pending) return;
      var policy = select.value || null;
      if (policy === state.override) { M.resetDraft(form); return; }
      pending = true; version++; error.textContent = "";
      M.busy(form, function () {
        return M.api("PUT", endpoint, { policy: policy, confirm: false }).then(function (review) {
          return M.confirm(M.t("delivery.confirm_title"), M.t("delivery.review", {
            old: review.before.effective.policy.id, new: review.after.effective.policy.id,
          }) + " " + M.t("delivery.refresh"), M.t("action.save"), false).then(function (ok) {
            if (!ok) return null;
            return M.api("PUT", endpoint, { policy: policy, confirm: true, review_hash: review.review_hash });
          });
        });
      }).then(function (review) {
        if (!review || !card.isConnected) return;
        state = review.after; select.value = state.override || ""; M.resetDraft(form); draw();
        M.toast(M.t("delivery.saved"));
        return M.api("GET", "api/users/" + encodeURIComponent(username)).then(function (latest) {
          if (card.isConnected && changed) changed(latest);
        });
      }, function () { /* M.busy explains the failure. */ }).finally(function () { pending = false; });
    });
    // The card is mounted by its caller before these replies settle.
    load();
    return { node: card, refresh: refresh };
  };
})();
