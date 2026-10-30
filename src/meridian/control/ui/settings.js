/* Meridian console: Settings: the fleet's title, appearance, and the owner's security
 * (password, the optional sign-in code, passkeys, and signed-in devices). */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;

  M.renderSettings = function (view) {
    M.mount(view, [
      h("div", { class: "page-head" }, h("div", {}, h("h1", { text: M.t("nav.settings") }))),
      h("div", { class: "cards" }, fleetCard(), appearanceCard()),
      M.noticeSection(),
      M.privacySection(),
      h("h2", { class: "section-title", text: M.t("settings.security") }),
      h("div", { class: "cards" }, passwordCard(), totpCard(), passkeyCard(), sessionsCard()),
      M.cliHint(),
    ]);
  };

  function fleetCard() {
    // The title is the fleet file's (`delivery.profile_title`): every apply sets it, so it is shown, not edited.
    var title = h("p", { class: "row-title" });
    var routing = h("p", { class: "row-title mono", dir: "ltr" });
    var routingExplanation = h("p", { class: "muted small" });
    var routingCommand = h("code", { dir: "ltr" });
    var publicUrl = h("p", { class: "mono small link-text" });
    var contactText = h("p", { class: "row-title", dir: "auto" });
    var contactLink = h("p", { class: "mono small link-text", dir: "ltr" });
    var error = h("p", { class: "form-error", role: "alert" });
    M.api("GET", "api/settings").then(function (settings) {
      title.textContent = settings.profile_title;
      var policy = settings.client_routing || "ru-split";
      routing.textContent = policy;
      routingExplanation.textContent = M.t(policy === "full" ? "settings.routing_full" : "settings.routing_ru_split");
      routingCommand.textContent = "meridian fleet routing " + (policy === "full" ? "ru-split" : "full");
      publicUrl.textContent = settings.public_url || M.t("settings.no_public_url");
      var contact = settings.owner_contact || {};
      contactText.textContent = contact.text || M.t("settings.contact_none");
      contactLink.textContent = contact.link || "";
    }, function (e) { error.textContent = M.errorText(e); });
    return h("section", { class: "card" },
      h("h2", { text: M.t("settings.fleet") }),
      h("p", { class: "label", text: M.t("settings.title") }), title,
      h("p", { class: "muted small", text: M.t("settings.title_hint") }),
      h("p", { class: "label", text: M.t("settings.client_routing") }), routing, routingExplanation,
      h("p", { class: "muted small" }, M.t("settings.routing_hint"), " ", routingCommand), error,
      h("p", { class: "label", text: M.t("settings.public_url") }), publicUrl,
      h("p", { class: "muted small", text: M.t("settings.public_url_hint") }),
      h("p", { class: "label", text: M.t("settings.owner_contact") }), contactText, contactLink,
      h("p", { class: "muted small" }, M.t("settings.contact_hint"), " ", h("code", { dir: "ltr", text: "meridian fleet contact" })));
  }

  function appearanceCard() {
    var theme = "auto";
    try { theme = localStorage.getItem("mrd.theme") || "auto"; } catch (e) { /* storage disabled */ }
    var themes = h("div", { class: "segmented", role: "radiogroup", "aria-label": M.t("settings.theme") },
      ["auto", "light", "dark"].map(function (value) {
        return h("button", { type: "button", role: "radio", "aria-checked": String(theme === value),
          class: theme === value ? "active" : "", onclick: function () { setTheme(value); } },
          M.t("theme." + value));
      }));
    var langs = h("div", { class: "segmented", role: "radiogroup", "aria-label": M.t("settings.language") },
      M.i18n.langs.map(function (code) {
        return h("button", { type: "button", role: "radio", lang: code, "aria-checked": String(M.i18n.lang === code),
          class: M.i18n.lang === code ? "active" : "", onclick: function () {
            M.tryLeave().then(function (leave) {
              if (leave) M.i18n.choose(code).then(function () { M.render(true); });
            });
          } }, M.i18n.names[code]);
      }));
    return h("section", { class: "card" },
      h("h2", { text: M.t("settings.appearance") }),
      h("p", { class: "label", text: M.t("settings.theme") }), themes,
      h("p", { class: "label", text: M.t("settings.language") }), langs);
  }

  function setTheme(value) {
    M.tryLeave().then(function (leave) { if (leave) applyTheme(value); });
  }

  function applyTheme(value) {
    try {
      if (value === "auto") localStorage.removeItem("mrd.theme");
      else localStorage.setItem("mrd.theme", value);
    } catch (e) { /* storage disabled: this page only */ }
    if (value === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", value);
    M.render(true);
  }

  function passwordCard() {
    var password = h("input", { type: "password", autocomplete: "new-password", maxlength: "1024" });
    var again = h("input", { type: "password", autocomplete: "new-password", maxlength: "1024" });
    var error = h("p", { class: "form-error", role: "alert" });
    var form = h("form", { class: "stack", novalidate: true },
      M.field(M.t("auth.new_password"), password, M.t("auth.password_hint")),
      M.field(M.t("auth.repeat_password"), again),
      error,
      h("div", { class: "row-actions" },
        h("button", { class: "btn btn-primary btn-sm", type: "submit" }, M.t("settings.change_password"))));
    M.trackForm(form);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (password.value.length < 10) { error.textContent = M.t("error.weak_password"); return; }
      if (password.value !== again.value) { error.textContent = M.t("auth.mismatch"); return; }
      M.saveDraft(form, function () { return M.api("POST", "auth/password", { password: password.value }); })
        .then(function () {
          password.value = again.value = "";
          M.resetDraft(form);
          M.toast(M.t("settings.password_toast"));
        }, function () { /* shown in the form */ });
    });
    return h("section", { class: "card" }, h("h2", { text: M.t("settings.password") }),
      h("p", { class: "muted small", text: M.t("settings.password_text") }), form);
  }

  function totpCard() {
    var on = M.state.auth && M.state.auth.totp_enabled;
    var button = on
      ? h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: function () {
        M.confirm(M.t("totp.off_title"), M.t("totp.off_text"), M.t("totp.turn_off"), true).then(function (ok) {
          if (!ok) return;
          M.api("POST", "auth/totp/disable", {}).then(function () {
            M.state.auth.totp_enabled = false;
            M.toast(M.t("totp.disabled_toast"));
            M.render();
          }, function (e) { if (e.code !== "cancelled") M.toast(M.errorText(e), "error"); });
        });
      } }, M.t("totp.turn_off"))
      : h("button", { class: "btn btn-primary btn-sm", type: "button", onclick: function () {
        M.enrollTotp().then(function (enabled) { if (enabled) M.render(); });
      } }, M.t("totp.turn_on"));
    return h("section", { class: "card" },
      h("div", { class: "card-head" }, h("h2", { text: M.t("totp.title") }),
        M.pill(on ? "ok" : "muted", M.t(on ? "totp.on" : "totp.off"))),
      h("p", { class: "muted small", text: M.t("totp.offer_why") }),
      h("p", { class: "muted small", text: M.t(on ? "totp.on_text" : "totp.off_hint") }),
      h("div", { class: "row-actions" }, button));
  }

  function passkeyCard() {
    var state = (M.state.auth && M.state.auth.passkeys) || { reason: "no_domain" };
    return h("section", { class: "card" },
      h("div", { class: "card-head" }, h("h2", { text: M.t("passkeys.title") }), M.pill("muted", M.t("passkeys.unavailable"))),
      h("p", { class: "muted small", text: M.t("passkeys." + state.reason) }));
  }

  function sessionsCard() {
    var list = h("div", { class: "list list-plain" }, h("span", { class: "spinner" }));
    var card = h("section", { class: "card" },
      h("div", { class: "card-head" }, h("h2", { text: M.t("sessions.title") }),
        h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: function () {
          M.api("POST", "auth/sessions/revoke-others", {}).then(function (result) {
            M.toast(M.t("sessions.revoked_toast", { count: result.revoked }));
            load();
          }, function (e) { M.toast(M.errorText(e), "error"); });
        } }, M.t("sessions.sign_out_others"))),
      h("p", { class: "muted small", text: M.t("sessions.text") }),
      list);
    function load() {
      M.api("GET", "auth/sessions").then(function (data) {
        M.mount(list, (data.sessions || []).map(function (s) {
          return h("div", { class: "row row-compact" },
            h("div", { class: "row-main" },
              h("div", { class: "row-title" }, h("span", { class: "name", text: M.t("sessions.signed_in", { when: M.date(s.created_at, true) }) }),
                s.current ? M.pill("ok", M.t("sessions.this_device")) : null),
              h("div", { class: "row-sub" }, h("span", { text: M.t("sessions.active", { when: M.ago(s.last_seen_at) }) }))));
        }));
      }, function (e) { M.mount(list, h("p", { class: "form-error", text: M.errorText(e) })); });
    }
    load();
    return card;
  }
})();
