/* Meridian console: one person: share their link, QR, and page; status, expiry, limits,
 * traffic reset, a new link, and removal. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;

  function path(username, suffix) { return "api/users/" + encodeURIComponent(username) + (suffix || ""); }

  M.renderPerson = function (view, username) {
    M.mount(view, h("div", { class: "empty" }, h("span", { class: "spinner" })));
    Promise.all([M.api("GET", path(username)), M.api("GET", "ui/api/people/" + encodeURIComponent(username) + "/share")])
      .then(function (results) { draw(view, results[0], results[1]); }, function (e) {
        M.mount(view, [back(), h("div", { class: "notice notice-err", text: M.errorText(e) })]);
      });
  };

  function back() {
    return h("a", { class: "back", href: "#/people" }, M.icon("back"), M.t("nav.people"));
  }

  function reload(username) { M.renderPerson(document.getElementById("view"), username); }

  function act(username, work, toastKey) {
    return work().then(function () {
      M.toast(M.t(toastKey));
      reload(username);
    }, function (e) { if (e.code !== "cancelled") M.toast(M.errorText(e), "error"); });
  }

  function draw(view, user, share) {
    var name = user.username;
    M.mount(view, [
      back(),
      h("div", { class: "page-head" },
        h("div", {},
          h("h1", { text: M.displayName(name) }),
          h("p", { class: "muted" },
            M.displayName(name) !== name ? h("span", { class: "mono", text: name + " · " }) : null,
            M.t("person.since", { when: M.date(user.created_at) }))),
        M.statusPill(user.status)),
      shareCard(share),
      h("div", { class: "cards" }, usageCard(user), accessCard(user)),
      dangerCard(user, share.declared),
    ]);
  }

  function shareCard(share) {
    if (!share.subscription_url) {
      return h("section", { class: "card" }, h("h2", { text: M.t("person.share") }),
        h("p", { class: "muted", text: M.t("person.no_public_url") }));
    }
    return h("section", { class: "card" },
      h("h2", { text: M.t("person.share") }),
      h("p", { class: "muted small", text: M.t("person.share_text") }),
      h("div", { class: "share" },
        h("img", { class: "qr", src: share.qr_svg, alt: M.t("person.qr_alt"), width: "200", height: "200" }),
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
          ] : h("p", { class: "muted small", text: M.t("person.no_page") }))));
  }

  function usageCard(user) {
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
          if (ok) act(name, function () { return M.api("POST", path(name, "/reset-traffic"), {}); }, "person.reset_toast");
        });
      } }, M.t("person.reset_traffic")));
  }

  function accessCard(user) {
    var name = user.username;
    var disabled = user.status === "disabled";
    var expiry = h("input", { type: "date", value: M.dateValue(user.expire_at) });
    var limits = M.limitFields(user);
    var form = h("form", { class: "stack", novalidate: true },
      M.field(M.t("people.expiry"), expiry, M.expiryText(user)),
      limits.nodes,
      h("p", { class: "form-error", role: "alert" }),
      h("div", { class: "row-actions" },
        h("button", { class: "btn btn-primary btn-sm", type: "submit" }, M.t("action.save")),
        user.expire_at ? h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: function () {
          act(name, function () { return M.api("PATCH", path(name), { expire_at: null }); }, "person.saved_toast");
        } }, M.t("person.clear_expiry")) : null));
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var change = limits.value();
      change.expire_at = M.endOfDay(expiry.value);
      M.busy(form, function () { return M.api("PATCH", path(name), change); }).then(function () {
        M.toast(M.t("person.saved_toast"));
        reload(name);
      }, function () { /* shown in the form */ });
    });
    return h("section", { class: "card" },
      h("div", { class: "card-head" },
        h("h2", { text: M.t("person.access") }),
        h("button", { class: "btn btn-sm " + (disabled ? "btn-primary" : "btn-ghost"), type: "button",
          onclick: function () {
            act(name, function () {
              return M.api("PATCH", path(name), { status: disabled ? "active" : "disabled" });
            }, disabled ? "person.enabled_toast" : "person.disabled_toast");
          } }, M.t(disabled ? "person.enable" : "person.disable"))),
      h("p", { class: "muted small", text: M.t("person.status_" + user.status) }),
      form);
  }

  /* `declared` comes with this person's share view, fresh from the service: the CLI moves a declared
   * person's connection page with their link, so a new link and removal are its commands. */
  function dangerCard(user, declared) {
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
              if (ok) act(name, function () { return M.api("POST", path(name, "/reissue"), {}); }, "person.reissued_toast");
            });
        } }, M.t("person.reissue"))),
      h("div", { class: "danger-row" },
        h("div", {}, h("p", { class: "strong", text: M.t("person.remove") }),
          h("p", { class: "muted small", text: M.t(declared ? "person.remove_declared" : "person.remove_text") }),
          declared ? M.command("meridian client remove " + name) : null),
        declared ? null : h("button", { class: "btn btn-danger btn-sm", type: "button", onclick: function () {
          M.confirm(M.t("person.remove_title", { name: M.displayName(name) }), M.t("person.remove_confirm"),
            M.t("person.remove"), true).then(function (ok) {
            if (!ok) return;
            M.api("DELETE", path(name)).then(function () {
              M.toast(M.t("person.removed_toast"));
              M.go("people");
            }, function (e) { M.toast(M.errorText(e), "error"); });
          });
        } }, M.t("person.remove"))));
  }
})();
