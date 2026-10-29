/* Subscription device counts and details on the person view. Privacy settings belong to Settings. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;

  function path(username, suffix) { return "api/users/" + encodeURIComponent(username) + (suffix || ""); }

  function refresh(username) { M.renderPerson(document.getElementById("view"), username); }

  function removeButton(username, key, count) {
    var all = key === null;
    var button = h("button", { class: "btn btn-ghost btn-sm", type: "button", disabled: count === 0,
      onclick: function () {
        M.confirm(M.t(all ? "devices.clear_title" : "devices.remove_title"),
          M.t(all ? "devices.clear_text" : "devices.remove_text"),
          M.t(all ? "devices.clear" : "devices.remove"), true).then(function (ok) {
          if (!ok) return;
          button.disabled = true;
          var request = all ? M.api("DELETE", path(username, "/devices")) :
            M.api("DELETE", path(username, "/devices/" + encodeURIComponent(key)));
          return request.then(function (result) {
            M.toast(M.t("devices.removed_toast", { count: result.deleted }));
            refresh(username);
          }, function (e) { if (e.code !== "cancelled") M.toast(M.errorText(e), "error"); })
            .then(function () { button.disabled = count === 0; });
        });
      } }, M.t(all ? "devices.clear" : "devices.remove"));
    return button;
  }

  function details(device, username) {
    var system = [device.platform, device.os_version].filter(Boolean).join(" ");
    return h("div", { class: "stack-sm" },
      h("div", { class: "card-head" },
        h("bdi", { class: "mono link-text", text: device.device_key }),
        removeButton(username, device.device_key, 1)),
      h("dl", { class: "facts" },
        h("dt", { text: M.t("devices.system") }),
        h("dd", { class: "link-text", text: system || M.t("devices.unknown") }),
        h("dt", { text: M.t("devices.model") }),
        h("dd", { class: "link-text", text: device.model || M.t("devices.unknown") }),
        h("dt", { text: M.t("devices.app") }),
        h("dd", { class: "link-text", text: device.user_agent || M.t("devices.unknown") }),
        h("dt", { text: M.t("devices.first_seen") }),
        h("dd", {}, h("bdi", { text: device.first_seen_at ? M.date(device.first_seen_at, true) : M.t("devices.pending") })),
        h("dt", { text: M.t("devices.last_seen") }),
        h("dd", {}, h("bdi", { text: device.last_seen_at ? M.date(device.last_seen_at, true) : M.t("devices.pending") }))));
  }

  function draw(list, user, result) {
    if (result.level === "off") {
      M.mount(list, h("p", { class: "muted small", text: M.t("devices.off") }));
      return;
    }
    var count = h("bdi", { text: M.t("devices.count", { count: result.count, limit: result.limit }) });
    var content = [h("div", { class: "card-head" }, count, removeButton(user.username, null, result.count))];
    if (result.level === "private") {
      content.push(h("p", { class: "muted small", text: M.t("devices.private") }));
    } else if (result.devices.length) {
      content.push(h("div", { class: "stack" }, result.devices.map(function (device) {
        return details(device, user.username);
      })));
    } else {
      content.push(h("p", { class: "muted small", text: M.t("devices.empty") }));
    }
    M.mount(list, content);
  }

  function capForm(user) {
    var original = user.device_limit === null || user.device_limit === undefined ? "" : String(user.device_limit);
    var limit = h("input", { type: "number", min: "1", max: "100", step: "1", inputmode: "numeric", value: original });
    var form = h("form", { class: "stack", novalidate: true },
      M.field(M.t("devices.limit"), limit, M.t("devices.limit_hint")),
      h("p", { class: "form-error", role: "alert" }),
      h("div", { class: "row-actions" },
        h("button", { class: "btn btn-primary btn-sm", type: "submit" }, M.t("action.save"))));
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var value = limit.value.trim();
      if ((limit.validity && limit.validity.badInput) ||
          (value && (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 100))) {
        form.querySelector(".form-error").textContent = M.t("devices.limit_invalid");
        return;
      }
      if (value === original) {
        M.toast(M.t("person.nothing_changed"));
        return;
      }
      M.busy(form, function () {
        return M.api("PATCH", path(user.username), { device_limit: value ? Number(value) : null });
      }).then(function () {
        M.toast(M.t("person.saved_toast"));
        refresh(user.username);
      }, function () { /* shown in the form */ });
    });
    return form;
  }

  M.devicesCard = function (user) {
    var list = h("div", { class: "stack" }, h("span", { class: "spinner" }));
    var card = h("section", { class: "card" }, h("h2", { text: M.t("devices.title") }), capForm(user), list);
    M.api("GET", path(user.username, "/devices")).then(function (result) {
      draw(list, user, result);
    }, function (e) {
      M.mount(list, h("p", { class: "notice notice-err", text: M.errorText(e) }));
    });
    return card;
  };
})();
