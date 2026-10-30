/* Meridian console: first setup, sign-in, re-confirmation, and the optional sign-in code.
 *
 * A setup link carries its one-time token in the URL fragment (#setup=…), which never
 * reaches the server's logs; the page reads it once and removes it from the address
 * bar and history before anything else runs. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;

  /* The token of a setup link, read once and stripped from the URL. */
  M.takeSetupToken = function () {
    var match = /^#setup=([A-Za-z0-9_-]{32,256})$/.exec(location.hash);
    if (!match) return "";
    history.replaceState(null, "", location.pathname + location.search);
    return match[1];
  };

  function card(children) {
    return h("main", { class: "auth" },
      h("div", { class: "auth-card" },
        h("div", { class: "brand" }, h("img", { src: "ui/icon.svg", alt: "", class: "brand-mark" }),
          h("span", { class: "brand-name", text: "Meridian" })),
        children),
      h("p", { class: "auth-foot", text: M.t("auth.footer") }));
  }

  function passwordInput(autocomplete) {
    return h("input", { type: "password", autocomplete: autocomplete, required: true, maxlength: "1024",
      spellcheck: "false", autocapitalize: "off" });
  }

  function codeInput() {
    return h("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", pattern: "[0-9 ]{6,7}",
      maxlength: "7", class: "code-input", required: true, spellcheck: "false" });
  }

  // -- screens ----------------------------------------------------------------------------

  /* Shown when there is nothing to sign in to yet, or the link is spent. */
  M.renderNotice = function (root, titleKey, textKey, command) {
    M.mount(root, card([
      h("h1", { text: M.t(titleKey) }),
      h("p", { class: "muted", text: M.t(textKey) }),
      command ? h("div", { class: "cli-box" }, M.icon("terminal"), M.command(command)) : null,
    ]));
  };

  M.renderSetup = function (root, token, purpose, done) {
    var password = passwordInput("new-password");
    var again = passwordInput("new-password");
    var error = h("p", { class: "form-error", role: "alert" });
    var form = h("form", { class: "stack", novalidate: true },
      M.field(M.t("auth.new_password"), password, M.t("auth.password_hint")),
      M.field(M.t("auth.repeat_password"), again),
      error,
      h("button", { class: "btn btn-primary btn-block", type: "submit" },
        M.t(purpose === "reset" ? "auth.reset_submit" : "auth.setup_submit")));
    M.trackForm(form);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (password.value.length < 10) { error.textContent = M.t("error.weak_password"); return; }
      if (password.value !== again.value) { error.textContent = M.t("auth.mismatch"); return; }
      M.saveDraft(form, function () {
        return M.rawApi("POST", "auth/setup", { token: token, password: password.value });
      }).then(function () { done(purpose); }, function () { /* shown under the form */ });
    });
    M.mount(root, card([
      h("h1", { text: M.t(purpose === "reset" ? "auth.reset_title" : "auth.setup_title") }),
      h("p", { class: "muted", text: M.t(purpose === "reset" ? "auth.reset_text" : "auth.setup_text") }),
      form,
    ]));
  };

  /* After first setup: offer the sign-in code, with why, and let the owner skip it. */
  M.renderTotpOffer = function (root, done) {
    M.mount(root, card([
      h("div", { class: "offer-icon" }, M.icon("shield")),
      h("h1", { text: M.t("totp.offer_title") }),
      h("p", { class: "muted", text: M.t("totp.offer_why") }),
      h("p", { class: "muted small", text: M.t("totp.offer_later") }),
      h("div", { class: "stack" },
        h("button", { class: "btn btn-primary btn-block", type: "button", onclick: function () {
          M.enrollTotp().then(function (enabled) { if (enabled) done(); });
        } }, M.t("totp.offer_yes")),
        h("button", { class: "btn btn-ghost btn-block", type: "button", onclick: done }, M.t("totp.offer_no"))),
    ]));
  };

  M.renderLogin = function (root, auth, done) {
    var password = passwordInput("current-password");
    var code = auth.totp_enabled ? codeInput() : null;
    var error = h("p", { class: "form-error", role: "alert" });
    var form = h("form", { class: "stack", novalidate: true },
      M.field(M.t("auth.password"), password),
      code ? M.field(M.t("auth.code"), code, M.t("auth.code_hint")) : null,
      error,
      h("button", { class: "btn btn-primary btn-block", type: "submit" }, M.t("auth.sign_in")));
    M.trackForm(form);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      M.saveDraft(form, function () {
        return M.rawApi("POST", "auth/login", { password: password.value, code: code ? code.value : "" });
      }).then(done, function () { password.select(); });
    });
    M.mount(root, card([
      h("h1", { text: M.t("auth.sign_in_title") }),
      form,
      h("details", { class: "forgot" },
        h("summary", { text: M.t("auth.forgot") }),
        h("p", { class: "muted small", text: M.t("auth.forgot_text") }),
        M.command("meridian panel --setup-link")),
    ]));
    password.focus();
  };

  // -- re-confirmation ----------------------------------------------------------------------

  /* Ask for the password (and code) again before a sensitive change; resolves once confirmed. */
  M.confirmIdentity = function () {
    var totp = M.state.auth && M.state.auth.totp_enabled;
    return M.dialog(M.t("reauth.title"), function (close) {
      var password = passwordInput("current-password");
      var code = totp ? codeInput() : null;
      var form = h("form", { class: "stack", novalidate: true },
        h("p", { class: "muted", text: M.t("reauth.text") }),
        M.field(M.t("auth.password"), password),
        code ? M.field(M.t("auth.code"), code) : null,
        h("p", { class: "form-error", role: "alert" }),
        h("div", { class: "actions" },
          h("button", { class: "btn btn-ghost", type: "button", onclick: function () { close(false); } },
            M.t("action.cancel")),
          h("button", { class: "btn btn-primary", type: "submit" }, M.t("reauth.submit"))));
      M.trackForm(form);
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        M.saveDraft(form, function () {
          return M.rawApi("POST", "auth/confirm", { password: password.value, code: code ? code.value : "" });
        }).then(function () { close(true); }, function () { /* shown in the form */ });
      });
      return form;
    }).then(function (ok) {
      if (!ok) throw new M.ApiError(403, "cancelled", "");
    });
  };

  // -- sign-in code (TOTP) ------------------------------------------------------------------

  function grouped(secret) { return secret.replace(/(.{4})/g, "$1 ").trim(); }

  /* Set up an authenticator: scan or type the secret, then confirm one code. Resolves true once on. */
  M.enrollTotp = function () {
    return M.api("POST", "auth/totp/begin", {}).then(function (enrollment) {
      return M.dialog(M.t("totp.enroll_title"), function (close) {
        var code = codeInput();
        var form = h("form", { class: "stack", novalidate: true },
          h("ol", { class: "steps" },
            h("li", { text: M.t("totp.step_app") }),
            h("li", { text: M.t("totp.step_scan") }),
            h("li", { text: M.t("totp.step_code") })),
          h("div", { class: "totp-grid" },
            h("img", { class: "qr", src: enrollment.qr_svg, alt: M.t("totp.qr_alt"), width: "184", height: "184" }),
            h("div", { class: "stack-sm" },
              h("p", { class: "label", text: M.t("totp.secret_label") }),
              h("code", { class: "secret", text: grouped(enrollment.secret) }),
              h("button", { class: "btn btn-ghost btn-sm", type: "button",
                onclick: function () { M.copy(enrollment.secret); } }, M.icon("copy"), M.t("action.copy")))),
          M.field(M.t("auth.code"), code),
          h("p", { class: "form-error", role: "alert" }),
          h("div", { class: "actions" },
            h("button", { class: "btn btn-ghost", type: "button", onclick: function () { close(false); } },
              M.t("action.cancel")),
            h("button", { class: "btn btn-primary", type: "submit" }, M.t("totp.enable"))));
        form.addEventListener("submit", function (e) {
          e.preventDefault();
          M.busy(form, function () { return M.api("POST", "auth/totp/enable", { code: code.value }); })
            .then(function () {
              if (M.state.auth) M.state.auth.totp_enabled = true;
              M.toast(M.t("totp.enabled_toast"));
              close(true);
            }, function () { code.select(); });
        });
        return form;
      }, { wide: true });
    }).then(Boolean, function (error) {
      if (error.code !== "cancelled") M.toast(M.errorText(error), "error");
      return false;
    });
  };
})();
