/* Bulk choices reuse the person form's byte and UTC validation. */
(function () {
  "use strict";
  var M = window.M, h = M.h;

  function choose(name, choices) {
    return h("select", { name: name }, choices.map(function (item) { return h("option", { value: item[0], text: item[1] }); }));
  }
  function number(name, max) {
    return h("input", { name: name, type: "number", min: "1", max: String(max), step: "1", inputmode: "numeric" });
  }
  function integer(input, max) {
    var raw = input.value.trim();
    if (input.validity.badInput || !/^[0-9]+$/.test(raw) || Number(raw) < 1 || Number(raw) > max) return null;
    return Number(raw);
  }
  M.bulkActionFields = function (kind) {
    var nodes = h("div", { class: "stack" }), error = h("p", { class: "form-error", role: "alert" }), read;
    function invalid(key) { error.textContent = M.t(key); return null; }
    if (kind === "extend") {
      var unit = choose("extend-unit", [["days", M.t("bulk.days")], ["months", M.t("bulk.months")]]);
      var amount = number("extend-amount", 3650);
      unit.addEventListener("change", function () { amount.max = unit.value === "days" ? "3650" : "120"; });
      nodes.appendChild(M.field(M.t("bulk.extend_unit"), unit));
      nodes.appendChild(M.field(M.t("bulk.amount"), amount));
      nodes.appendChild(h("p", { class: "hint", text: M.t("bulk.pending_months") }));
      read = function () {
        var value = integer(amount, unit.value === "days" ? 3650 : 120);
        if (value === null) return invalid("bulk.amount_invalid");
        var result = {}; result[unit.value] = value; return result;
      };
    } else if (kind === "expiry") {
      var expiry = M.expiryFields(null);
      nodes.appendChild(expiry.nodes);
      read = function () {
        var value = expiry.value();
        return value === undefined ? null : { expire_at: value };
      };
    } else if (kind === "tag") {
      var tag = h("input", { name: "bulk-tag", type: "text", maxlength: "16", autocomplete: "off" });
      nodes.appendChild(M.field(M.t("person.tag"), tag, M.t("person.tag_hint")));
      read = function () {
        var value = tag.value.trim().toUpperCase();
        return value && !/^[A-Z0-9_]{1,16}$/.test(value) ? invalid("person.tag_invalid") : { tag: value };
      };
    } else if (kind === "devices") {
      var deviceMode = choose("bulk-device-mode", [["inherit", M.t("devices.inherit")], ["unlimited", M.t("devices.unlimited")], ["limited", M.t("devices.custom")]]);
      var devices = number("bulk-device-limit", 100);
      function deviceSync() { devices.disabled = deviceMode.value !== "limited"; }
      deviceMode.addEventListener("change", deviceSync); deviceSync();
      nodes.appendChild(M.field(M.t("devices.limit"), deviceMode, M.t("devices.choice_hint")));
      nodes.appendChild(M.field(M.t("devices.limit"), devices));
      read = function () {
        if (deviceMode.value === "inherit") return { device_limit: null };
        if (deviceMode.value === "unlimited") return { device_limit: 0 };
        var value = integer(devices, 100);
        return value === null ? invalid("devices.limit_invalid") : { device_limit: value };
      };
    } else if (kind === "quota") {
      var quotaMode = choose("bulk-quota-mode", [["keep", M.t("bulk.keep")], ["unlimited", M.t("people.unlimited")], ["limited", M.t("people.limited")]]);
      var quotaAmount = h("input", { name: "bulk-quota", type: "text", maxlength: "64", inputmode: "decimal" });
      var quotaUnit = choose("bulk-quota-unit", [["GiB", M.t("unit.gib")], ["TiB", M.t("unit.tib")]]);
      var strategy = choose("bulk-reset", [["keep", M.t("bulk.keep")]].concat(["no_reset", "day", "week", "month", "month_rolling"].map(function (key) { return [key, M.t("reset." + key)]; })));
      var anchor = number("bulk-reset-day", 31);
      function quotaSync() { quotaAmount.disabled = quotaUnit.disabled = quotaMode.value !== "limited"; }
      quotaMode.addEventListener("change", quotaSync); quotaSync();
      nodes.appendChild(M.field(M.t("people.limit_mode"), quotaMode));
      nodes.appendChild(M.field(M.t("people.limit_gb"), quotaAmount));
      nodes.appendChild(M.field(M.t("people.limit_unit"), quotaUnit));
      nodes.appendChild(M.field(M.t("people.reset"), strategy));
      nodes.appendChild(M.field(M.t("lifecycle.reset_anchor"), anchor, M.t("bulk.keep_hint")));
      read = function () {
        var result = {};
        if (quotaMode.value === "unlimited") result.traffic_limit_bytes = 0;
        if (quotaMode.value === "limited") {
          var parsed = M.parseQuotaBytes(quotaAmount.value, quotaUnit.value);
          if (parsed.error) return invalid(parsed.error);
          result.traffic_limit_bytes = parsed.bytes;
        }
        if (strategy.value !== "keep") result.traffic_limit_strategy = strategy.value;
        if (anchor.value.trim() || anchor.validity.badInput) {
          var day = integer(anchor, 31);
          if (day === null) return invalid("lifecycle.invalid_reset_anchor");
          result.traffic_reset_anchor_day = day;
        }
        return Object.keys(result).length ? result : invalid("bulk.quota_required");
      };
    } else if (kind === "renewal") {
      var renewalMode = choose("bulk-renewal-mode", [["off", M.t("bulk.none")], ["days", M.t("bulk.days")], ["months", M.t("bulk.months")]]);
      var interval = number("bulk-renewal-interval", 3650), cap = number("bulk-renewal-cap", 10000);
      function renewalSync() {
        interval.disabled = cap.disabled = renewalMode.value === "off";
        interval.max = renewalMode.value === "months" ? "120" : "3650";
      }
      renewalMode.addEventListener("change", renewalSync); renewalSync();
      nodes.appendChild(M.field(M.t("lifecycle.automatic_renewal"), renewalMode));
      nodes.appendChild(M.field(M.t("bulk.amount"), interval));
      nodes.appendChild(M.field(M.t("lifecycle.renewal_cap"), cap, M.t("bulk.unlimited_hint")));
      read = function () {
        var result = { renewal_interval_days: null, renewal_interval_months: null, renewal_limit: null };
        if (renewalMode.value === "off") return result;
        var value = integer(interval, renewalMode.value === "months" ? 120 : 3650);
        if (value === null) return invalid("bulk.amount_invalid");
        result["renewal_interval_" + renewalMode.value] = value;
        if (cap.value.trim() || cap.validity.badInput) {
          var limit = integer(cap, 10000);
          if (limit === null) return invalid("lifecycle.invalid_renewal_cap");
          result.renewal_limit = limit;
        }
        return result;
      };
    } else read = function () { return {}; };
    nodes.appendChild(error);
    return { nodes: nodes, value: function () { error.textContent = ""; return read(); } };
  };
})();
