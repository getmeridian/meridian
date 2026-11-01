/* Meridian console: the People list and adding a person.
 * People live in this service: everything about them can change here. The ones the
 * deployer's fleet file declares are marked, because removing them is the CLI's job. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;
  var USERNAME = /^(?:[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}|\.[A-Za-z0-9_-][A-Za-z0-9_.-]{0,62}|\.\.[A-Za-z0-9_.-]{1,62})$/;  // models.USERNAME_PATTERN
  var GIB = 1024 * 1024 * 1024;
  var QUOTA_UNITS = { GiB: GIB, TiB: GIB * 1024 };

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

  /* Keep the person screen's existing helper name; amounts are now binary GiB, unrounded. */
  M.gbText = function (bytes) {
    return bytes ? String(bytes / GIB) : "";
  };

  function quotaBytes(text, unit) {
    var normalized = text.trim().replace(/[۰-۹٠-٩]/g, function (digit) {
      var code = digit.charCodeAt(0);
      return String(code >= 1776 ? code - 1776 : code - 1632);
    }).replace(/٫/g, ".");
    var match = /^(\d+)(?:\.(\d+))?$/.exec(normalized);
    if (!match || normalized.length > 64 || !M.has(QUOTA_UNITS, unit)) return { error: "people.limit_invalid" };
    var fraction = match[2] || "";
    var numerator = BigInt(match[1] + fraction) * BigInt(QUOTA_UNITS[unit]);
    var denominator = BigInt(10) ** BigInt(fraction.length);
    var bytes = numerator / denominator;
    var remainder = numerator % denominator;
    // Match Decimal's ROUND_HALF_EVEN in core.client_limits, including half-byte inputs.
    if (remainder * BigInt(2) > denominator || (remainder * BigInt(2) === denominator && bytes % BigInt(2))) bytes++;
    if (bytes < BigInt(1)) return { error: "people.limit_invalid" };
    // JSON numbers must preserve every byte. Larger existing CLI limits stay untouched on unrelated edits.
    if (bytes > BigInt(Number.MAX_SAFE_INTEGER)) return { error: "people.limit_too_large" };
    return { bytes: Number(bytes) };
  }

  /* Limits as a form block, shared by "add" and the person screen. With a person, `value()`
   * holds only what was edited, so saving another field never rewrites the limit. */
  M.limitFields = function (user) {
    var initialMode = user && user.traffic_limit_bytes ? "limited" : "unlimited";
    var initialText = user ? M.gbText(user.traffic_limit_bytes) : "";
    var mode = h("select", {},
      h("option", { value: "unlimited", text: M.t("people.unlimited") }),
      h("option", { value: "limited", text: M.t("people.limited") }));
    mode.value = initialMode;
    var unit = h("select", {}, Object.keys(QUOTA_UNITS).map(function (key) {
      return h("option", { value: key, text: M.t("unit." + key.toLowerCase()) });
    }));
    unit.value = "GiB";
    var gb = h("input", { type: "text", maxlength: "64", inputmode: "decimal", autocomplete: "off",
      value: user ? M.gbText(user.traffic_limit_bytes) : "" });
    var strategy = h("select", {},
      ["no_reset", "day", "week", "month"].map(function (s) {
        return h("option", { value: s, selected: user ? user.traffic_limit_strategy === s : s === "month",
          text: M.t("reset." + s) });
      }));
    var amountField = M.field(M.t("people.limit_gb"), gb, M.t("people.limit_hint"));
    var error = h("p", { class: "field-error", role: "alert", id: gb.id + "-error" });
    gb.setAttribute("aria-describedby", error.id);
    var nodes = h("div", { class: "grid-2 limit-fields" },
      M.field(M.t("people.limit_mode"), mode), M.field(M.t("people.limit_unit"), unit),
      h("div", {}, amountField, error), M.field(M.t("people.reset"), strategy));
    function edited() { return mode.value !== initialMode || gb.value !== initialText || unit.value !== "GiB"; }
    function validate() {
      var unlimited = mode.value === "unlimited";
      gb.disabled = unit.disabled = unlimited;
      var parsed = unlimited || (user && !edited()) ? {} : quotaBytes(gb.value, unit.value);
      var message = parsed.error ? M.t(parsed.error) : "";
      if (gb.validity && gb.validity.badInput) message = M.t("people.limit_invalid");
      gb.setCustomValidity(message);
      gb.setAttribute("aria-invalid", message ? "true" : "false");
      error.textContent = message;
      return !message;
    }
    [gb, mode, unit].forEach(function (input) { input.addEventListener(input === gb ? "input" : "change", validate); });
    validate();
    // The person screen owns its submit handler. Install a capturing field guard after its form is built.
    Promise.resolve().then(function () {
      if (gb.form) gb.form.addEventListener("submit", function (event) {
        if (validate()) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        gb.focus();
        gb.reportValidity();
      }, true);
    });
    return {
      gb: gb,
      mode: mode,
      unit: unit,
      strategy: strategy,
      nodes: nodes,
      value: function () {
        if (!validate()) throw new Error(error.textContent);
        var change = {};
        if (!user || edited()) {
          var bytes = mode.value === "unlimited" ? 0 : quotaBytes(gb.value, unit.value).bytes;
          if (!user || bytes !== user.traffic_limit_bytes) change.traffic_limit_bytes = bytes;
        }
        if (!user || strategy.value !== user.traffic_limit_strategy) change.traffic_limit_strategy = strategy.value;
        return change;
      },
    };
  };

  M.addPerson = function () {
    M.dialog(M.t("people.add_title"), function (close) {
      var name = h("input", { type: "text", required: true, maxlength: "64", pattern: "[A-Za-z0-9_.\\-]{1,64}",
        autocomplete: "off", autocapitalize: "off", spellcheck: "false" });
      var expiry = M.expiryFields();
      var limits = M.limitFields(null);
      var form = h("form", { class: "stack", novalidate: true },
        M.field(M.t("people.name"), name, M.t("people.name_hint")),
        expiry.nodes,
        limits.nodes,
        h("p", { class: "form-error", role: "alert" }),
        h("div", { class: "actions" },
          h("button", { class: "btn btn-ghost", type: "button", onclick: function () { close(); } },
            M.t("action.cancel")),
          h("button", { class: "btn btn-primary", type: "submit" }, M.t("people.add_submit"))));
      M.trackForm(form);
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var username = name.value.trim();
        if (!USERNAME.test(username)) {
          form.querySelector(".form-error").textContent = M.t("people.name_invalid");
          return;
        }
        // One request with the settings: create-then-PATCH left an unlimited person when the PATCH failed.
        var body = limits.value();
        body.username = username;
        body.expire_at = expiry.value();
        M.saveDraft(form, function () { return M.api("POST", "api/users", body); }).then(function () {
          close();
          M.toast(M.t("people.added_toast", { name: username }));
          M.go("people", username);
        }, function () { /* shown in the form */ });
      });
      return form;
    });
  };
})();
