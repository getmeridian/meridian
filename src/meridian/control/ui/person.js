/* Meridian console: one person: share their link, QR, and page; status, expiry, limits,
 * traffic reset, a new link, and removal. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;
  var generation = 0;

  function path(username, suffix) { return "api/users/" + encodeURIComponent(username) + (suffix || ""); }

  M.renderPerson = function (view, username) {
    var screen = { view: view, username: username, generation: ++generation, stopped: false };
    view.meridianPersonGeneration = screen.generation;
    M.mount(view, h("div", { class: "empty" }, h("span", { class: "spinner" })));
    Promise.all([M.api("GET", path(username)), M.api("GET", "ui/api/people/" + encodeURIComponent(username) + "/share")])
      .then(function (results) { if (current(screen)) draw(screen, results[0], results[1]); }, function (e) {
        if (!current(screen)) return;
        M.mount(view, [back(), h("div", { class: "notice notice-err", text: M.errorText(e) })]);
      });
    return function () { screen.stopped = true; };
  };

  function back() {
    return h("a", { class: "back", href: "#/people" }, M.icon("back"), M.t("nav.people"));
  }

  function current(screen) {
    return !screen.stopped && screen.view.isConnected && screen.view.meridianPersonGeneration === screen.generation;
  }

  function completed(screen, user, toastKey, label) {
    if (toastKey === "person.enabled_toast" && user.status === "expired") toastKey = "person.enabled_expired_toast";
    if (toastKey === "person.enabled_toast" && user.status === "limited") toastKey = "person.enabled_limited_toast";
    M.toast(M.t(toastKey, { name: label }));
    if (!current(screen)) return;
    draw(screen, user, screen.share);
    if (toastKey === "person.reissued_toast") {
      M.api("GET", "ui/api/people/" + encodeURIComponent(screen.username) + "/share").then(function (share) {
        if (current(screen)) draw(screen, screen.user, share);
      }, function (e) { if (current(screen)) M.toast(M.errorText(e), "error"); });
    }
  }

  function act(screen, work, toastKey) {
    if (!current(screen)) return Promise.resolve();
    var label = M.displayName(screen.username);
    return work().then(function (user) {
      completed(screen, user, toastKey, label);
    }, function (e) { if (e.code !== "cancelled") M.toast(M.errorText(e), "error"); });
  }

  function draw(screen, user, share) {
    screen.user = user;
    screen.share = share;
    var name = user.username;
    M.mount(screen.view, [
      back(),
      h("div", { class: "page-head" },
        h("div", {},
          h("h1", { text: M.displayName(name) }),
          h("p", { class: "muted" },
            M.displayName(name) !== name ? h("span", { class: "mono", text: name + " · " }) : null,
            M.t("person.since", { when: M.date(user.created_at) }))),
        M.statusPill(user.status)),
      shareCard(share, name),
      h("div", { class: "cards" }, usageCard(user, screen), accessCard(user, screen)),
      metadataCard(user, screen),
      M.personHistory(name),
      M.devicesCard(user),
      dangerCard(user, share.declared, screen),
    ]);
  }

  function shareCard(share, username) {
    if (!share.subscription_url) {
      return h("section", { class: "card" }, h("h2", { text: M.t("person.share") }),
        h("p", { class: "muted", text: M.t("person.no_public_url") }), M.connectionDisclosure(share));
    }
    return h("section", { class: "card" },
      h("h2", { text: M.t("person.share") }),
      h("p", { class: "muted small", text: M.t("person.share_text") }),
      h("div", { class: "share" },
        h("div", { class: "stack-sm share-qr" },
          h("img", { class: "qr", src: share.qr_svg, alt: M.t("person.qr_alt"), width: "200", height: "200" }),
          M.qrExportActions(share.qr_svg, { name: M.displayName(username), username: username,
            title: share.profile_title || (M.state.fleet && M.state.fleet.profile_title) || "Meridian" })),
        h("div", { class: "stack-sm share-links" },
          h("p", { class: "label", text: M.t("person.subscription") }),
          h("code", { class: "link-text", text: share.subscription_url }),
          h("div", { class: "row-actions" },
            h("button", { class: "btn btn-soft btn-sm", type: "button",
              onclick: function () { M.copy(share.subscription_url); } }, M.icon("copy"), M.t("person.copy_link"))),
          share.page_url ? [
            h("p", { class: "label", text: M.t("person.page") }),
            h("div", { class: "row-actions" },
              h("a", { class: "btn btn-ghost btn-sm", href: share.page_url, target: "_blank", rel: "noopener noreferrer" },
                M.icon("link"), M.t("person.open_page")),
              h("button", { class: "btn btn-ghost btn-sm", type: "button",
                onclick: function () { M.copy(share.page_url); } }, M.icon("copy"), M.t("person.copy_page"))),
          ] : h("p", { class: "muted small", text: M.t("person.no_page") }))), M.connectionDisclosure(share));
  }

  function usageCard(user, screen) {
    var name = user.username;
    return h("section", { class: "card" },
      h("h2", { text: M.t("person.traffic") }),
      M.usage(user),
      h("dl", { class: "facts" },
        h("dt", { text: M.t("people.reset") }), h("dd", { text: M.t("reset." + user.traffic_limit_strategy) }),
        h("dt", { text: M.t("person.period_began") }),
        h("dd", { text: user.traffic_reset_at ? M.date(user.traffic_reset_at, true) : M.t("person.since_created") }),
        h("dt", { text: M.t("person.lifetime") }), h("dd", { text: M.bytes(user.lifetime_traffic_bytes) }),
        h("dt", { text: M.t("person.last_online") }), h("dd", { text: M.ago(user.online_at) })),
      h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: function () {
        M.confirm(M.t("person.reset_title"), M.t("person.reset_text"), M.t("person.reset_traffic")).then(function (ok) {
          if (ok) act(screen, function () { return M.api("POST", path(name, "/reset-traffic"), {}); }, "person.reset_toast");
        });
      } }, M.t("person.reset_traffic")));
  }

  function accessCard(user, screen) {
    var name = user.username;
    var disabled = user.status === "disabled";
    var expiry = M.expiryFields(user.expire_at, name);
    var limits = M.limitFields(user);
    var form = h("form", { class: "stack", novalidate: true },
      expiry.nodes,
      limits.nodes,
      h("p", { class: "form-error", role: "alert" }),
      h("div", { class: "row-actions" },
        h("button", { class: "btn btn-primary btn-sm", type: "submit" }, M.t("action.save"))));
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var change = limits.value();
      Object.assign(change, expiry.change());
      if (!Object.keys(change).length) {
        M.toast(M.t("person.nothing_changed"));
        return;
      }
      var label = M.displayName(name);
      M.busy(form, function () { return M.api("PATCH", path(name), change); }).then(function (changed) {
        completed(screen, changed, "person.saved_toast", label);
      }, function () { /* shown in the form */ });
    });
    return h("section", { class: "card" },
      h("div", { class: "card-head" },
        h("h2", { text: M.t("person.access") }),
        h("button", { class: "btn btn-sm " + (disabled ? "btn-primary" : "btn-ghost"), type: "button",
          onclick: function () {
            act(screen, function () {
              return M.api("PATCH", path(name), { status: disabled ? "active" : "disabled" });
            }, disabled ? "person.enabled_toast" : "person.disabled_toast");
          } }, M.t(disabled ? "person.enable" : "person.disable"))),
      h("p", { class: "muted small", text: M.t("person.status_" + user.status) }),
      form);
  }

  function metadataCard(user, screen) {
    var initialNote = user.note || "", initialTag = user.tag || "";
    var note = h("textarea", { name: "note", rows: "4" }, initialNote);
    var tag = h("input", { name: "tag", type: "text", value: initialTag, dir: "ltr", autocomplete: "off" });
    var error = h("p", { class: "form-error", role: "alert" });
    var form = h("form", { class: "stack", novalidate: true },
      M.field(M.t("person.note"), note, M.t("person.note_hint")),
      M.field(M.t("person.tag"), tag, M.t("person.tag_hint")), error,
      h("button", { class: "btn btn-primary btn-sm", type: "submit" }, M.t("action.save")));
    function validate() {
      var characters = Array.from(note.value);
      var invalidText = characters.some(function (character) {
        var code = character.codePointAt(0);
        return code !== 10 && (code < 32 || code >= 127 && code <= 159 || code >= 55296 && code <= 57343);
      });
      var noteError = characters.length > 500 || invalidText ? M.t("person.note_invalid") : "";
      var tagError = /^[A-Z0-9_]{0,16}$/.test(tag.value) ? "" : M.t("person.tag_invalid");
      note.setCustomValidity(noteError);
      tag.setCustomValidity(tagError);
      note.setAttribute("aria-invalid", noteError ? "true" : "false");
      tag.setAttribute("aria-invalid", tagError ? "true" : "false");
      error.textContent = noteError || tagError;
      return !error.textContent;
    }
    note.addEventListener("input", validate);
    tag.addEventListener("input", validate);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (!validate()) { (note.validationMessage ? note : tag).focus(); return; }
      var change = {};
      if (note.value !== initialNote) change.note = note.value;
      if (tag.value !== initialTag) change.tag = tag.value;
      if (!Object.keys(change).length) { M.toast(M.t("person.nothing_changed")); return; }
      var label = M.displayName(user.username);
      M.busy(form, function () { return M.api("PATCH", path(user.username), change); }).then(function (changed) {
        completed(screen, changed, "person.saved_toast", label);
      }, function () { /* shown in the form */ });
    });
    return h("section", { class: "card" }, h("h2", { text: M.t("person.metadata") }),
      h("p", { class: "muted small", text: M.t("person.metadata_private") }), form);
  }

  /* `declared` comes with this person's share view, fresh from the service: the CLI moves a declared
   * person's connection page with their link, so a new link and removal are its commands. */
  function dangerCard(user, declared, screen) {
    var name = user.username;
    return h("section", { class: "card card-danger" },
      h("h2", { text: M.t("person.danger") }),
      h("div", { class: "danger-row" },
        h("div", {}, h("p", { class: "strong", text: M.t("person.reissue") }),
          h("p", { class: "muted small", text: M.t(declared ? "person.reissue_declared" : "person.reissue_text") }),
          declared ? M.command("meridian client reissue " + name) : null),
        declared ? null : h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: function () {
          M.confirm(M.t("person.reissue_title"), M.t("person.reissue_confirm"), M.t("person.reissue"), true)
            .then(function (ok) {
              if (ok) act(screen, function () { return M.api("POST", path(name, "/reissue"), {}); }, "person.reissued_toast");
            });
        } }, M.t("person.reissue"))),
      h("div", { class: "danger-row" },
        h("div", {}, h("p", { class: "strong", text: M.t("person.remove") }),
          h("p", { class: "muted small", text: M.t(declared ? "person.remove_declared" : "person.remove_text") }),
          declared ? M.command("meridian client remove " + name) : null),
        declared ? null : h("button", { class: "btn btn-danger btn-sm", type: "button", onclick: function () {
          M.confirmCutOffRemoval(name).then(function (choice) {
            if (!choice || !current(screen)) return;
            var label = M.displayName(name);
            M.api("DELETE", path(name), { cut_off: choice.cut_off }).then(function (changed) {
              M.toast(M.t("person.removed_toast", { name: label }));
              if (!current(screen)) return;
              M.go("people");
              if (changed.commands) M.showCommandResults(changed.commands);
            }, function (e) { M.toast(M.errorText(e), "error"); });
          }, function (e) { M.toast(M.errorText(e), "error"); });
        } }, M.t("person.remove"))));
  }
})();
