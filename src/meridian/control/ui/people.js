/* Meridian console: the People list and adding a person.
 * People live in this service: everything about them can change here. The ones the
 * deployer's fleet file declares are marked, because removing them is the CLI's job. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;
  var USERNAME = /^[A-Za-z0-9_.-]{1,64}$/;
  var GB = 1e9;

  M.displayName = function (username) {
    var names = (M.state.fleet && M.state.fleet.display_names) || {};
    return M.has(names, username) && names[username] ? String(names[username]) : username;
  };

  M.isDeclared = function (username) {
    var declared = (M.state.fleet && M.state.fleet.declared_users) || [];
    return declared.indexOf(username) >= 0;
  };

  M.statusPill = function (status) {
    var kinds = { active: "ok", disabled: "muted", limited: "warn", expired: "err" };
    return M.pill(kinds[status] || "muted", M.t("status." + status));
  };

  /* Used of limit as a bar; no bar without a limit. */
  M.usage = function (user) {
    var limit = user.traffic_limit_bytes || 0;
    var used = user.used_traffic_bytes || 0;
    if (!limit) {
      return h("div", { class: "usage" }, h("span", { class: "usage-text", text: M.bytes(used) }),
        h("span", { class: "muted small", text: M.t("people.no_limit") }));
    }
    var ratio = Math.min(1, used / limit);
    var bar = h("span", { class: "bar-fill" + (ratio >= 1 ? " bar-full" : ratio > 0.85 ? " bar-high" : "") });
    bar.style.width = Math.round(ratio * 100) + "%";
    return h("div", { class: "usage" },
      h("span", { class: "usage-text", text: M.t("people.used_of", { used: M.bytes(used), limit: M.bytes(limit) }) }),
      h("span", { class: "bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100",
        "aria-valuenow": String(Math.round(ratio * 100)) }, bar));
  };

  M.expiryText = function (user) {
    if (!user.expire_at) return M.t("people.no_expiry");
    var past = new Date(user.expire_at).getTime() <= Date.now();
    return M.t(past ? "people.expired_on" : "people.expires", { when: M.ago(user.expire_at) });
  };

  M.renderPeople = function (view) {
    var list = h("div", { class: "list" }, h("div", { class: "empty" }, h("span", { class: "spinner" })));
    var search = h("input", { type: "search", class: "search", placeholder: M.t("people.search"),
      "aria-label": M.t("people.search"), autocomplete: "off" });
    var users = [];

    function draw() {
      var q = search.value.trim().toLowerCase();
      var shown = users.filter(function (u) {
        return !q || u.username.toLowerCase().indexOf(q) >= 0 || M.displayName(u.username).toLowerCase().indexOf(q) >= 0;
      });
      if (!users.length) {
        M.mount(list, h("div", { class: "empty" }, h("p", { text: M.t("people.empty") })));
        return;
      }
      if (!shown.length) {
        M.mount(list, h("div", { class: "empty" }, h("p", { text: M.t("people.no_match") })));
        return;
      }
      M.mount(list, shown.map(function (user) {
        return h("a", { class: "row row-link", href: "#/people/" + encodeURIComponent(user.username) },
          h("div", { class: "row-main" },
            h("div", { class: "row-title" },
              h("span", { class: "name", text: M.displayName(user.username) }),
              M.isDeclared(user.username) ? h("span", { class: "tag", title: M.t("people.declared_hint"),
                text: M.t("people.declared") }) : null),
            h("div", { class: "row-sub" },
              h("span", { text: M.expiryText(user) }),
              h("span", { text: M.t("people.online", { when: M.ago(user.online_at) }) }))),
          h("div", { class: "row-side" }, M.statusPill(user.status), M.usage(user)));
      }));
    }

    search.addEventListener("input", draw);
    M.mount(view, [
      h("div", { class: "page-head" },
        h("div", {}, h("h1", { text: M.t("nav.people") }), h("p", { class: "muted", text: M.t("people.lede") })),
        h("button", { class: "btn btn-primary", type: "button", onclick: addPerson }, M.icon("plus"),
          M.t("people.add"))),
      search,
      list,
    ]);
    // The fleet view comes again with the list: who is declared changes whenever the deployer applies.
    Promise.all([M.api("GET", "api/users"), M.api("GET", "ui/api/fleet").then(function (fleet) {
      M.state.fleet = fleet;
    }, function () { /* keep the last fleet view; the person screen and the service decide */ })])
      .then(function (results) {
        users = results[0].users || [];
        draw();
      }, function (e) { M.mount(list, h("div", { class: "notice notice-err", text: M.errorText(e) })); });
  };

  /* Limits as a form block, shared by "add" and the person screen. */
  M.limitFields = function (user) {
    var gb = h("input", { type: "number", min: "0", step: "0.1", inputmode: "decimal",
      value: user && user.traffic_limit_bytes ? String(+(user.traffic_limit_bytes / GB).toFixed(2)) : "" });
    var strategy = h("select", {},
      ["no_reset", "day", "week", "month"].map(function (s) {
        return h("option", { value: s, selected: user ? user.traffic_limit_strategy === s : s === "month",
          text: M.t("reset." + s) });
      }));
    return {
      gb: gb,
      strategy: strategy,
      nodes: h("div", { class: "grid-2" },
        M.field(M.t("people.limit_gb"), gb, M.t("people.limit_hint")),
        M.field(M.t("people.reset"), strategy)),
      value: function () {
        var bytes = Math.round((parseFloat(gb.value) || 0) * GB);
        return { traffic_limit_bytes: Math.max(0, bytes), traffic_limit_strategy: strategy.value };
      },
    };
  };

  /* A date input's end of day, local time, as the protocol's whole-second UTC timestamp. */
  M.endOfDay = function (dateText) {
    if (!dateText) return null;
    var parts = dateText.split("-").map(Number);
    var d = new Date(parts[0], parts[1] - 1, parts[2], 23, 59, 59);
    return d.toISOString().replace(/\.\d{3}Z$/, "Z");
  };

  M.dateValue = function (timestamp) {
    if (!timestamp) return "";
    var d = new Date(timestamp);
    var pad = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  };

  function addPerson() {
    M.dialog(M.t("people.add_title"), function (close) {
      var name = h("input", { type: "text", required: true, maxlength: "64", pattern: "[A-Za-z0-9_.\\-]{1,64}",
        autocomplete: "off", autocapitalize: "off", spellcheck: "false" });
      var expiry = h("input", { type: "date" });
      var limits = M.limitFields(null);
      var form = h("form", { class: "stack", novalidate: true },
        M.field(M.t("people.name"), name, M.t("people.name_hint")),
        M.field(M.t("people.expiry"), expiry, M.t("people.expiry_hint")),
        limits.nodes,
        h("p", { class: "form-error", role: "alert" }),
        h("div", { class: "actions" },
          h("button", { class: "btn btn-ghost", type: "button", onclick: function () { close(); } },
            M.t("action.cancel")),
          h("button", { class: "btn btn-primary", type: "submit" }, M.t("people.add_submit"))));
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var username = name.value.trim();
        if (!USERNAME.test(username)) {
          form.querySelector(".form-error").textContent = M.t("people.name_invalid");
          return;
        }
        M.busy(form, function () {
          return M.api("GET", "api/users/" + encodeURIComponent(username)).then(function () {
            throw new M.ApiError(409, "user_exists", "");
          }, function (error) {
            if (error.status !== 404) throw error;
            return M.api("PUT", "api/users/" + encodeURIComponent(username));
          }).then(function () {
            var change = limits.value();
            if (expiry.value) change.expire_at = M.endOfDay(expiry.value);
            var defaults = !change.traffic_limit_bytes && change.traffic_limit_strategy === "no_reset";
            if (defaults && !change.expire_at) return null; // what a new person starts with already
            return M.api("PATCH", "api/users/" + encodeURIComponent(username), change);
          });
        }).then(function () {
          close();
          M.toast(M.t("people.added_toast", { name: username }));
          M.go("people", username);
        }, function () { /* shown in the form */ });
      });
      return form;
    });
  }
})();
