/* One share view for the person page and Overview's picker; credentials stay in memory. */
(function () {
  "use strict";
  var M = window.M, h = M.h;

  M.shareCard = function (share, username) {
    if (!share.subscription_url) {
      return h("div", { class: "stack" },
        h("p", { class: "muted", text: M.t("person.no_public_url") }), M.connectionDisclosure(share));
    }
    return h("div", { class: "stack" },
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
  };

  M.openShare = function (username) {
    return M.dialog(M.t("overview.share_person", { name: M.displayName(username) }), function () {
      var body = h("div", { class: "share-dialog-body" }, h("span", { class: "spinner" }));
      function load() {
        M.mount(body, h("span", { class: "spinner" }));
        M.api("GET", "ui/api/people/" + encodeURIComponent(username) + "/share").then(function (share) {
          if (body.isConnected) M.mount(body, M.shareCard(share, username));
        }, function (error) {
          if (!body.isConnected) return;
          M.mount(body, [h("p", { class: "form-error", role: "alert", text: M.errorText(error) }),
            h("button", { class: "btn btn-ghost", type: "button", onclick: load }, M.t("action.retry"))]);
        });
      }
      load();
      return body;
    }, { wide: true });
  };
})();
