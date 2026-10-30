/* Individual handoff credentials, in canonical subscription order. No requests or storage. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;
  var FORMATS = { "xray-json": "Xray JSON", mihomo: "Mihomo", singbox: "sing-box" };

  M.connectionDisclosure = function (share) {
    var entries = share.connections || [];
    var formats = share.formats || [];
    if (!entries.length && !formats.length) return null;
    return h("details", { class: "individual-connections" },
      h("summary", { text: M.t("person.connections") }),
      h("div", { class: "stack-sm" }, entries.map(function (entry) {
        return h("section", { class: "connection-entry stack-sm" },
          h("h3", { class: "connection-remark", dir: "auto", text: entry.remark }),
          h("p", { class: "muted small", text: entry.protocol ? M.t("protocol." + entry.protocol)
            : M.t("person.connection_unavailable") }),
          entry.available ? h("code", { class: "link-text", dir: "ltr", text: entry.uri }) : null,
          h("div", { class: "row-actions" },
            h("button", { class: "btn btn-soft btn-sm", type: "button", disabled: !entry.available,
              "aria-label": M.t("person.copy_connection", { name: entry.remark }),
              onclick: function () { M.copy(entry.uri); } }, M.icon("copy"), M.t("action.copy")),
            h("button", { class: "btn btn-ghost btn-sm", type: "button", disabled: !entry.available,
              onclick: function () {
                M.dialog(M.t("person.connection_qr", { name: entry.remark }), function () {
                  return h("img", { class: "qr connection-qr", src: entry.qr_svg,
                    width: "280", height: "280", alt: M.t("person.qr_alt") });
                });
              } }, M.t("person.show_qr"))));
      }), formats.length ? [h("h3", { text: M.t("person.connection_formats") }),
        formats.map(function (format) {
          if (!M.has(FORMATS, format.format)) return null;
          return h("div", { class: "stack-sm" },
            h("a", { href: format.url, target: "_blank", rel: "noopener noreferrer", text: FORMATS[format.format] }),
            h("code", { class: "link-text", dir: "ltr", text: format.url }),
            h("button", { class: "btn btn-ghost btn-sm", type: "button",
              onclick: function () { M.copy(format.url); } }, M.icon("copy"), M.t("person.copy_link")));
        })] : null));
  };
})();
