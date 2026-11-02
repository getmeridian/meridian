/* Owner passkeys use the browser's WebAuthn API and this console's own service. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;

  M.passkeySupported = function () {
    return !!(window.isSecureContext && window.PublicKeyCredential && navigator.credentials &&
      navigator.credentials.create && navigator.credentials.get);
  };

  function bytes(value) {
    var raw = atob(value.replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, function (c) { return c.charCodeAt(0); });
  }

  function encoded(value) {
    var raw = "";
    new Uint8Array(value).forEach(function (byte) { raw += String.fromCharCode(byte); });
    return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function options(publicKey, create) {
    var result = Object.assign({}, publicKey, { challenge: bytes(publicKey.challenge) });
    if (create) result.user = Object.assign({}, publicKey.user, { id: bytes(publicKey.user.id) });
    var field = create ? "excludeCredentials" : "allowCredentials";
    result[field] = (publicKey[field] || []).map(function (credential) {
      return Object.assign({}, credential, { id: bytes(credential.id) });
    });
    return result;
  }

  function proof(credential, create) {
    if (!credential) throw new M.ApiError(400, "passkey_cancelled", "");
    var response = { clientDataJSON: encoded(credential.response.clientDataJSON) };
    if (create) response.attestationObject = encoded(credential.response.attestationObject);
    else {
      response.authenticatorData = encoded(credential.response.authenticatorData);
      response.signature = encoded(credential.response.signature);
      var userHandle = credential.response.userHandle;
      response.userHandle = userHandle && userHandle.byteLength > 0 ? encoded(userHandle) : null;
    }
    return { type: "public-key", id: encoded(credential.rawId), rawId: encoded(credential.rawId), response: response };
  }

  function browserError(error) {
    if (error && (error.name === "NotAllowedError" || error.name === "AbortError")) {
      throw new M.ApiError(400, "passkey_cancelled", "");
    }
    if (error && (error.name === "NotSupportedError" || error.name === "SecurityError")) {
      throw new M.ApiError(400, "passkey_browser", "");
    }
    throw error;
  }

  M.usePasskey = function (confirm) {
    if (!M.passkeySupported()) return Promise.reject(new M.ApiError(400, "passkey_browser", ""));
    var beginPath = confirm ? "auth/passkeys/confirm/begin" : "auth/passkeys/login/begin";
    var finishPath = confirm ? "auth/passkeys/confirm/finish" : "auth/passkeys/login/finish";
    return M.rawApi("POST", beginPath, {}).then(function (begin) {
      return navigator.credentials.get({ publicKey: options(begin.public_key, false) }).then(function (credential) {
        return M.rawApi("POST", finishPath, { challenge_id: begin.challenge_id, credential: proof(credential, false) });
      });
    }).catch(browserError);
  };

  function add(name) {
    return M.api("POST", "auth/passkeys/register/begin", {}).then(function (begin) {
      return navigator.credentials.create({ publicKey: options(begin.public_key, true) }).then(function (credential) {
        return M.api("POST", "auth/passkeys/register/finish", {
          challenge_id: begin.challenge_id, name: name, credential: proof(credential, true),
        });
      });
    }).catch(browserError);
  }

  function nameDialog(title, initial, work) {
    return M.dialog(title, function (close) {
      var name = h("input", { type: "text", required: true, maxlength: "64", value: initial, autocomplete: "off" });
      var error = h("p", { class: "form-error", role: "alert" });
      var form = h("form", { class: "stack", novalidate: true },
        M.field(M.t("passkeys.name"), name, M.t("passkeys.name_hint")), error,
        h("div", { class: "actions" },
          h("button", { class: "btn btn-ghost", type: "button", onclick: function () { close(false); } }, M.t("action.cancel")),
          h("button", { class: "btn btn-primary", type: "submit" }, M.t("action.save"))));
      M.trackForm(form);
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        if (!name.value.trim() || name.value.trim().length > 64) { error.textContent = M.t("passkeys.name_hint"); return; }
        close.lock(true);
        name.disabled = true;
        M.saveDraft(form, function () { return work(name.value.trim()); }).then(function () {
          close.lock(false);
          close(true);
        }, function () { close.lock(false); name.disabled = false; });
      });
      return form;
    });
  }

  M.passkeyCard = function () {
    var state = (M.state.auth && M.state.auth.passkeys) || { available: false, reason: "no_domain" };
    var supported = M.passkeySupported();
    var list = h("div", { class: "list list-plain" }, h("span", { class: "spinner" }));
    var latest = M.latestOnly();
    var card = h("section", { class: "card" },
      h("div", { class: "card-head" }, h("h2", { text: M.t("passkeys.title") }),
        M.pill(state.available && supported ? "ok" : "muted", M.t(state.available && supported ? "passkeys.ready" : "passkeys.unavailable"))),
      h("p", { class: "muted small", text: M.t(state.available ? "passkeys.caption" : "passkeys.domain_caption") }),
      M.learnMore([h("p", { text: M.t(state.available ? "passkeys.text" : "passkeys.no_domain") }),
        h("p", { text: M.t("passkeys.recovery") })]),
      state.available && !supported ? h("p", { class: "muted small", text: M.t("error.passkey_browser") }) : null,
      list,
      state.available && supported ? h("div", { class: "row-actions" },
        h("button", { class: "btn btn-primary btn-sm", type: "button", onclick: function () {
          nameDialog(M.t("passkeys.add"), "", add).then(function (saved) {
            if (saved) refresh("passkeys.added");
          });
        } }, M.t("passkeys.add"))) : null);

    function refresh(key) {
      M.toast(M.t(key));
      M.rawApi("GET", "auth/state").then(function (auth) { M.state.auth = auth; load(); }, function () { load(); });
    }

    function load() {
      latest(M.api("GET", "auth/passkeys")).then(function (data) {
        if (!card.isConnected) return;
        var records = data.passkeys || [];
        M.mount(list, records.length ? records.map(row) : h("p", { class: "muted small", text: M.t("passkeys.empty") }));
      }, function (error) { M.mount(list, h("p", { class: "form-error", text: M.errorText(error) })); });
    }

    function row(record) {
      return h("div", { class: "row row-compact" },
        h("div", { class: "row-main" },
          h("div", { class: "row-title" }, h("bdi", { class: "name", text: record.name })),
          h("p", { class: "row-sub", text: M.t("passkeys.created", { when: M.date(record.created_at, true) }) }),
          h("p", { class: "row-sub", text: record.last_used_at ? M.t("passkeys.used", { when: M.date(record.last_used_at, true) }) : M.t("passkeys.never") }),
          h("p", { class: "row-sub" }, h("bdi", { text: record.rp_id }))),
        h("div", { class: "row-actions" },
          h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: function () {
            nameDialog(M.t("passkeys.rename"), record.name, function (name) {
              return M.api("PATCH", "auth/passkeys/" + encodeURIComponent(record.id), { name: name });
            }).then(function (saved) { if (saved) refresh("passkeys.renamed"); });
          } }, M.t("passkeys.rename")),
          h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: function () {
            M.confirm(M.t("passkeys.remove"), M.t("passkeys.remove_text", { name: record.name }), M.t("passkeys.remove"), true).then(function (ok) {
              if (!ok) return;
              M.api("DELETE", "auth/passkeys/" + encodeURIComponent(record.id)).then(function () {
                refresh("passkeys.removed");
              }, function (error) { if (error.code !== "cancelled") M.toast(M.errorText(error), "error"); });
            });
          } }, M.t("passkeys.remove"))));
    }
    load();
    return card;
  };
})();
