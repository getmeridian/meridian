/* Meridian console: Settings: the fleet's information and the owner's security
 * (password, the optional sign-in code, passkeys, and signed-in devices). */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;

  M.renderSettings = function (view, initial) {
    var mounted = {}, settings = null;
    function readSettings() { return settings || (settings = M.api("GET", "api/settings")); }
    var builders = {
      general: function () { return [generalCard(readSettings()), M.cliHint()]; },
      notice: function () { return M.noticeSection(); },
      language: function () { return M.languageSection(); },
      privacy: function () { return M.privacySection(); },
      security: function () {
        return [h("h2", { class: "section-title", text: M.t("settings.security") }),
          h("div", { class: "cards" }, passwordCard(), totpCard(), M.passkeyCard(), sessionsCard())];
      },
      advanced: function () { return [M.formatPreviewSection(), advancedFacts(readSettings())]; },
    };
    var sections = M.settingsSections(function (next, previous, panel, oldPanel) {
      if (oldPanel && M.hasUnsavedEdits(oldPanel)) {
        M.discardDrafts(oldPanel); M.clear(oldPanel); mounted[previous] = false;
      }
      if (!mounted[next]) { M.mount(panel, builders[next]()); mounted[next] = true; }
    });
    M.mount(view, [h("div", { class: "page-head" }, h("h1", { text: M.t("nav.settings") })), sections.node]);
    sections.select(initial);
    var stop = function () { sections.stop(); M.discardDrafts(view); };
    stop.route = function (route) { sections.select(route.arg); };
    return stop;
  };

  function routingFacts(settings) {
    var policy = settings.default_policy || settings.client_routing || "ru-split";
    var named = policy !== "full" && policy !== "ru-split";
    return { policy: policy,
      explanation: M.t(named ? "policy.named" : policy === "full" ? "settings.routing_full" : "settings.routing_ru_split"),
      command: named ? "meridian fleet routing set POLICY" : "meridian fleet routing " + (policy === "full" ? "ru-split" : "full") };
  }

  function fact(label, value, caption, command, tip) {
    return M.keyValue(label, h("div", { class: "stack-sm" }, value,
      h("div", { class: "settings-command muted small" }, h("span", { text: caption }), command)), tip);
  }

  function generalCard(read) {
    var title = h("bdi", { class: "settings-fact-value" }), interval = h("bdi", {});
    var publicUrl = h("bdi", { class: "link-text", dir: "ltr" });
    var contactText = h("bdi", { dir: "auto" }), contactLink = h("bdi", { class: "link-text", dir: "ltr" });
    var routing = h("bdi", { class: "settings-fact-value mono", dir: "ltr" });
    var explanation = h("p"), command = h("span"), error = h("p", { class: "form-error", role: "alert" });
    function show(state) {
      title.textContent = state.profile_title;
      interval.textContent = M.count(state.update_interval_hours);
      var contact = state.owner_contact || {};
      contactText.textContent = contact.text || (contact.link ? "" : M.t("settings.contact_none"));
      contactLink.textContent = contact.link || ""; contactLink.hidden = !contact.link;
    }
    var edit = h("button", { type: "button", class: "btn btn-ghost btn-sm", onclick: function () {
      M.editPresentation().then(function (state) {
        if (!state || typeof state !== "object") return;
        show(state);
        if (M.state.fleet) M.state.fleet.profile_title = state.profile_title;
        if (M.updateFleetTitle) M.updateFleetTitle();
        document.title = state.profile_title;
      });
    } }, M.t("presentation.edit"));
    read.then(function (settings) {
      show(settings); publicUrl.textContent = settings.public_url || M.t("settings.no_public_url");
      var facts = routingFacts(settings);
      routing.textContent = facts.policy; explanation.textContent = facts.explanation;
      M.mount(command, M.command(facts.command));
    }, function (e) { error.textContent = M.errorText(e); });
    return h("section", { class: "card" },
      h("div", { class: "card-head" }, h("h2", { text: M.t("settings.general") }), edit), error,
      h("dl", { class: "settings-facts" },
        M.keyValue(M.t("settings.title"), title),
        M.keyValue(M.t("presentation.interval"), interval),
        M.keyValue(M.t("settings.owner_contact"), h("div", { class: "stack-sm" }, contactText, contactLink), M.t("tip.owner_contact")),
        fact(M.t("settings.public_url"), publicUrl, M.t("settings.address_caption"), M.command("meridian setup"), M.t("tip.public_url")),
        fact(M.t("settings.client_routing"), routing, M.t("settings.routing_hint"), command, M.t("tip.client_routing"))),
      M.learnMore([h("p", { text: M.t("settings.public_url_hint") }), explanation]));
  }

  function advancedFacts(read) {
    var routing = h("bdi", { class: "settings-fact-value mono", dir: "ltr" });
    var explanation = h("p", { class: "muted small" }), command = h("div");
    var responseRules = h("ol", { class: "stack-sm", "data-response-rules": "" });
    var error = h("p", { class: "form-error", role: "alert" });
    read.then(function (settings) {
      var facts = routingFacts(settings);
      routing.textContent = facts.policy; explanation.textContent = facts.explanation;
      M.mount(command, M.command(facts.command));
      M.mount(responseRules, (settings.response_rules || []).map(function (rule, index) {
        return h("li", { value: index + 1 }, h("bdi", { text: rule.name }), " · ", M.t("response_rules.action_" + rule.action.kind));
      }));
      if (!(settings.response_rules || []).length) M.mount(responseRules, h("li", { text: M.t("response_rules.reason_built_in_selector") }));
    }, function (e) { error.textContent = M.errorText(e); });
    return h("div", { class: "stack" },
      h("section", { class: "card" }, M.titled("h2", M.t("settings.client_routing"), M.t("tip.client_routing")), routing, explanation, command, error),
      h("section", { class: "card" }, M.titled("h2", M.t("response_rules.title"), M.t("tip.response_rules")),
        h("p", { class: "muted small", text: M.t("response_rules.reviewed_hint") }), responseRules));
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
      h("div", { class: "card-head" }, M.titled("h2", M.t("totp.title"), M.t("tip.totp")),
        M.pill(on ? "ok" : "muted", M.t(on ? "totp.on" : "totp.off"))),
      h("p", { class: "muted small", text: M.t(on ? "totp.on_text" : "totp.off_hint") }),
      M.learnMore(h("p", { text: M.t("totp.offer_why") })),
      h("div", { class: "row-actions" }, button));
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
