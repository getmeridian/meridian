/* Token permissions, service previews, and one-time credential handoff. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;

  function label(permission) {
    var lang = M.i18n.lang || "en";
    return (permission.labels && (permission.labels[lang] || permission.labels.en)) || permission.capability;
  }

  function permissions(grants, catalog) {
    return h("ul", { class: "stack-sm" }, grants.map(function (capability) {
      var permission = catalog.capabilities.find(function (row) { return row.capability === capability; });
      return h("li", { text: permission ? label(permission) : capability });
    }));
  }

  M.renderTokens = function (view) {
    if (!view.isConnected) return;
    var list = h("div", {}, h("span", { class: "spinner" }));
    var create = h("button", { class: "btn btn-primary", type: "button", "data-token-create": "", disabled: true },
      M.icon("plus"), M.t("tokens.create"));
    M.mount(view, [h("div", { class: "page-head" },
      h("div", {}, M.titled("h1", M.t("nav.tokens"), M.t("tip.tokens")), h("p", { class: "muted small", text: M.t("tokens.lede") })), create), list]);
    Promise.all([M.api("GET", "api/tokens"), M.api("GET", "api/token-capabilities")]).then(function (data) {
      if (!view.isConnected) return;
      var catalog = data[1];
      var tokens = data[0].tokens || [];
      create.disabled = false;
      create.addEventListener("click", function () { createToken(view, catalog); });
      M.mount(list, tokens.length ? h("ul", { class: "list record-list" }, tokens.map(function (token) { return tokenRow(view, token, catalog); }))
        : M.emptyState(M.t("tokens.empty"), M.t("tokens.empty_hint")));
    }, function (error) { M.mount(list, h("p", { class: "form-error", role: "alert", text: M.errorText(error) })); });
  };

  function isolate(value) { return "\u2068" + value + "\u2069"; }

  function tokenRow(view, token, catalog) {
    var expired = token.expires_at && Date.parse(token.expires_at) <= Date.now();
    var grants = token.capabilities || catalog.presets[token.scope] || [];
    var kind = token.revoked_at ? "neutral" : expired ? "warn" : "success";
    return h("li", { class: "row record-row" + (token.revoked_at || expired ? " row-dim" : ""), "data-record": token.id },
      h("div", { class: "record-main" },
        h("div", { class: "row-title" }, h("bdi", { class: "record-name name", text: token.name }),
          M.statusChip(kind, M.t(token.revoked_at ? "tokens.status_revoked" : expired ? "tokens.status_expired" : "tokens.status_active"))),
        h("div", { class: "record-facts row-sub" },
          h("span", { text: token.scope ? M.t("scope." + token.scope) : M.t("tokens.action_count", { count: isolate(M.count(grants.length)) }) }),
          h("span", { text: M.t("tokens.created", { when: isolate(M.date(token.created_at)) }) }),
          h("span", { text: token.expires_at
            ? M.t(expired ? "tokens.expired" : "tokens.expires", { when: isolate(M.date(token.expires_at, true)) })
            : M.t("tokens.expires_never") }),
          h("span", { text: token.revoked_at ? M.t("tokens.revoked", { when: isolate(M.date(token.revoked_at)) })
            : M.t("tokens.used", { when: isolate(M.ago(token.last_used_at)) }) }))),
      M.rowMenu(token.name, [
        h("button", { class: "btn btn-ghost", type: "button", "data-record-action": "permissions", onclick: function () {
          M.dialog(M.t("tokens.permissions"), function (close) {
            return [h("bdi", { class: "strong", text: token.name }), permissions(grants, catalog),
              h("div", { class: "actions" }, h("button", { class: "btn btn-primary", type: "button",
                onclick: function () { close(); } }, M.t("action.done")))];
          });
        } }, M.t("tokens.permissions")),
        token.revoked_at ? null : h("button", { class: "btn btn-ghost", type: "button", "data-record-action": "revoke", onclick: function () {
          M.confirm(M.t("tokens.revoke_title", { name: token.name }), M.t("tokens.revoke_text"), M.t("tokens.revoke"), true).then(function (ok) {
            if (!ok) return;
            M.api("DELETE", "api/tokens/" + token.id).then(function () {
              M.toast(M.t("tokens.revoked_toast")); M.renderTokens(view);
            }, function (error) { M.toast(M.errorText(error), "error"); });
          });
        } }, M.t("tokens.revoke")),
      ]));
  }

  function createToken(view, catalog) {
    M.dialog(M.t("tokens.create_title"), function (close) {
      var issuing = false, reviewed = null, reviewedBody = "";
      var selected = new Set(["people.read"]);
      var name = h("input", { type: "text", required: true, maxlength: "64", autocomplete: "off" });
      var scope = h("select", {}, ["custom", "read", "propose", "approve", "people"].map(function (value) {
        return h("option", { value: value, text: M.t("scope." + value) });
      }));
      var expiry = h("select", {}, [7, 30, 90, null].map(function (days) {
        return h("option", { value: days === null ? "never" : String(days), selected: days === 90,
          text: days === null ? M.t("tokens.expires_never") : M.t("tokens.expiry_days", { days: M.count(days) }) });
      }));
      var checks = [];
      var choices = h("fieldset", { class: "stack-sm token-choices" }, h("legend", { text: M.t("tokens.actions") }),
        catalog.capabilities.filter(function (row) { return row.console_delegable; }).map(function (row) {
          var checkbox = h("input", { type: "checkbox", checked: selected.has(row.capability), value: row.capability });
          checks.push(checkbox);
          checkbox.addEventListener("change", function () {
            if (checkbox.checked) selected.add(row.capability); else selected.delete(row.capability);
            invalidate();
          });
          return h("label", { class: "check-row" }, checkbox, h("span", { text: label(row) }));
        }));
      var previewBox = h("div", { class: "stack-sm", role: "status" });
      var submit = h("button", { class: "btn btn-primary", type: "submit" }, M.t("tokens.review"));
      var form = h("form", { class: "stack", novalidate: true },
        M.field(M.t("tokens.name"), name, M.t("tokens.name_hint")),
        M.field(M.t("tokens.scope"), scope), choices,
        h("p", { class: "muted small", text: M.t("tokens.cli_permissions") }),
        M.field(M.t("tokens.expiry"), expiry), previewBox,
        h("p", { class: "form-error", role: "alert" }),
        h("div", { class: "actions" },
          h("button", { class: "btn btn-ghost", type: "button", onclick: function () { close(); } }, M.t("action.cancel")), submit));

      function body() {
        var result = { name: name.value.trim() };
        if (scope.value === "custom") result.capabilities = Array.from(selected).sort(); else result.scope = scope.value;
        if (expiry.value === "never") result.never_expires = true; else result.expires_in_days = Number(expiry.value);
        return result;
      }
      function invalidate() {
        reviewed = null;
        submit.textContent = M.t("tokens.review");
        M.mount(previewBox, [h("p", { class: "label", text: M.t("tokens.permissions") }), permissions(Array.from(selected).sort(), catalog)]);
      }
      function inputs(enabled) {
        form.querySelectorAll("input, select").forEach(function (input) { input.disabled = !enabled; });
        if (enabled && scope.value !== "custom") checks.forEach(function (checkbox) { checkbox.disabled = true; });
      }
      scope.addEventListener("change", function () {
        selected = new Set(scope.value === "custom" ? ["people.read"] : catalog.presets[scope.value]);
        checks.forEach(function (checkbox) { checkbox.checked = selected.has(checkbox.value); checkbox.disabled = scope.value !== "custom"; });
        invalidate();
      });
      name.addEventListener("input", invalidate);
      expiry.addEventListener("change", invalidate);
      invalidate();
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        if (issuing) return;
        var request = body();
        if (!request.name) { form.querySelector(".form-error").textContent = M.t("tokens.name_required"); return; }
        if (request.capabilities && !request.capabilities.length) { form.querySelector(".form-error").textContent = M.t("tokens.action_required"); return; }
        var fingerprint = JSON.stringify(request);
        var creating = reviewed && reviewedBody === fingerprint;
        issuing = true; close.lock(true); inputs(false);
        M.busy(form, function () {
          if (!creating) return M.api("POST", "api/tokens/preview", request).then(function (result) {
            if (!result || !Array.isArray(result.permissions) || !Array.isArray(result.capabilities)) {
              throw new M.ApiError(502, "invalid_request", "");
            }
            return result;
          });
          // The absolute expiry reviewed by the service is the one that is issued.
          delete request.expires_in_days;
          if (reviewed.expires_at) request.expires_at = reviewed.expires_at; else request.never_expires = true;
          return M.api("POST", "api/tokens", request);
        }).then(function (result) {
          issuing = false; close.lock(false); inputs(true);
          if (creating) { close(result && typeof result.token === "string" && result.token ? result : { uncertain: true }); return; }
          reviewed = result; reviewedBody = fingerprint;
          M.mount(previewBox, [h("p", { class: "label", text: M.t("tokens.permissions") }),
            h("ul", { class: "stack-sm" }, result.permissions.map(function (row) { return h("li", { text: label(row) }); })),
            h("p", { text: result.expires_at ? M.t("tokens.expires", { when: M.date(result.expires_at, true) }) : M.t("tokens.expires_never") })]);
          submit.textContent = M.t("tokens.create");
        }, function (error) {
          issuing = false; close.lock(false); inputs(true);
          if (creating && error && error.uncertain) close({ uncertain: true });
        });
      });
      return form;
    }).then(function (issued) {
      if (!issued) return;
      M.renderTokens(view);
      if (issued.uncertain) {
        return M.dialog(M.t("tokens.uncertain_title"), function (close) {
          return [h("p", { class: "notice notice-warn", text: M.t("tokens.uncertain_text") }),
            h("div", { class: "actions" }, h("button", { class: "btn btn-primary", type: "button", onclick: function () { close(); } }, M.t("action.done")))];
        });
      }
      M.dialog(M.t("tokens.secret_title"), function (close) {
        return [h("p", { class: "notice notice-warn", text: M.t("tokens.secret_once") }),
          h("code", { class: "secret secret-token", text: issued.token }),
          h("div", { class: "actions" },
            h("button", { class: "btn btn-ghost", type: "button", onclick: function () { M.copy(issued.token); } }, M.icon("copy"), M.t("action.copy")),
            h("button", { class: "btn btn-primary", type: "button", onclick: function () { close(); } }, M.t("action.done")))];
      });
    });
  }
})();
