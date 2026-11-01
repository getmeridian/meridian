/* Account dates come from the service's calculator; edits and previews stay in memory. */
(function () {
  "use strict";
  var M = window.M, h = M.h;
  var fields = ["traffic_reset_anchor_day", "activation_duration_days", "renewal_interval_days",
    "renewal_interval_months", "renewal_limit"];

  M.sameLifecycle = function (before, after) {
    return fields.every(function (field) { return before[field] === after[field]; });
  };

  function exactDate(value) {
    return h("span", { class: "stack-sm" }, h("bdi", { text: M.date(value, true) }),
      h("code", { dir: "ltr", text: value + " UTC" }));
  }

  M.firstUseText = function (days) {
    return M.t(days === 1 ? "lifecycle.valid_from_first_use_one" : "lifecycle.valid_from_first_use",
      { days: M.count(days) });
  };

  M.scheduleFacts = function (schedule) {
    if (!schedule) return null;
    var rows = [];
    function fact(key, value) { rows.push(h("dt", { text: M.t(key) }), h("dd", {}, value)); }
    if (schedule.activation_pending) fact("lifecycle.expiry",
      M.firstUseText(schedule.activation_duration_days));
    else if (schedule.expire_at) fact("lifecycle.expiry", exactDate(schedule.expire_at));
    if (schedule.next_reset_at) fact("lifecycle.next_reset", exactDate(schedule.next_reset_at));
    if (schedule.renewal_enabled) {
      fact("lifecycle.next_renewal", schedule.next_renewal_at ? exactDate(schedule.next_renewal_at) :
        M.t(schedule.activation_pending ? "lifecycle.waiting_first_use" : "lifecycle.no_renewals_left"));
      fact("lifecycle.renewals_left", schedule.renewals_left === null ? M.t("lifecycle.unlimited_renewals") :
        M.count(schedule.renewals_left));
    }
    return h("dl", { class: "facts lifecycle-dates" }, rows);
  };

  function integer(input, max, empty) {
    var text = input.value.trim();
    if (input.validity && input.validity.badInput) return false;
    return empty && !text || /^\d+$/.test(text) && Number(text) >= 1 && Number(text) <= max;
  }

  M.lifecycleFields = function (user, expiry, limits) {
    user = user || null;
    var initialMode = user && user.activation_duration_days && !user.expire_at ? "first_use" :
      user && user.expire_at ? "fixed" : "never";
    var mode = h("select", { name: "expiry_mode" },
      h("option", { value: "never", text: M.t("people.no_expiry") }),
      h("option", { value: "fixed", text: M.t("lifecycle.fixed_expiry") }),
      h("option", { value: "first_use", text: M.t("lifecycle.first_use") }));
    mode.value = initialMode;
    var initialDuration = user && user.activation_duration_days ? String(user.activation_duration_days) : "30";
    var duration = h("input", { name: "activation_duration_days", type: "number", min: "1", max: "3650",
      step: "1", inputmode: "numeric", value: initialDuration });
    var durationField = M.field(M.t("lifecycle.duration"), duration, M.t("lifecycle.first_use_hint"));
    var initialRenewal = user && user.renewal_interval_days ? "days" : user && user.renewal_interval_months ? "months" : "off";
    var renewal = h("select", { name: "renewal_mode" },
      ["off", "days", "months"].map(function (value) {
        return h("option", { value: value, text: M.t("lifecycle.renew_" + value) });
      }));
    renewal.value = initialRenewal;
    var initialInterval = String(user && (user.renewal_interval_days || user.renewal_interval_months) || 30);
    var interval = h("input", { name: "renewal_interval", type: "number", min: "1", step: "1",
      inputmode: "numeric", value: initialInterval });
    var initialCapMode = user && user.renewal_limit !== null && user.renewal_limit !== undefined ? "limited" : "unlimited";
    var capMode = h("select", { name: "renewal_cap_mode" },
      h("option", { value: "unlimited", text: M.t("lifecycle.unlimited_renewals") }),
      h("option", { value: "limited", text: M.t("lifecycle.capped_renewals") }));
    capMode.value = initialCapMode;
    var initialCap = String(user && user.renewal_limit || 1);
    var cap = h("input", { name: "renewal_limit", type: "number", min: "1", max: "10000", step: "1",
      inputmode: "numeric", value: initialCap });
    var renewalFields = h("div", { class: "grid-2" }, M.field(M.t("lifecycle.interval"), interval),
      M.field(M.t("lifecycle.renewal_cap"), capMode), M.field(M.t("lifecycle.renewal_count"), cap));
    var initialAnchor = user && user.traffic_reset_anchor_day ? String(user.traffic_reset_anchor_day) : "";
    var anchor = h("input", { name: "traffic_reset_anchor_day", type: "number", min: "1", max: "31",
      step: "1", inputmode: "numeric", value: initialAnchor });
    var anchorField = M.field(M.t("lifecycle.reset_day"), anchor, M.t("lifecycle.reset_day_hint"));
    var error = h("p", { class: "field-error lifecycle-error", role: "alert" });
    var preview = h("div", { class: "lifecycle-preview", "aria-live": "polite" }, M.scheduleFacts(user && user.schedule));
    var zone = new Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    var nodes = h("fieldset", { class: "stack-sm lifecycle-fields" },
      h("legend", { text: M.t("lifecycle.title") }), M.field(M.t("lifecycle.expiry_mode"), mode),
      expiry.nodes, durationField, M.field(M.t("lifecycle.automatic_renewal"), renewal, M.t("lifecycle.renewal_hint")),
      renewalFields, anchorField, error, h("p", { class: "hint", text: M.t("lifecycle.zone", { zone: zone }) }), preview);
    // The mode selector replaces the fixed editor's no-expiry choice.
    expiry.never.closest("label").hidden = true;
    function sync() {
      expiry.nodes.hidden = mode.value === "first_use";
      durationField.hidden = duration.disabled = mode.value !== "first_use";
      renewalFields.hidden = interval.disabled = capMode.disabled = renewal.value === "off";
      cap.disabled = renewal.value === "off" || capMode.value === "unlimited";
      interval.max = renewal.value === "months" ? "120" : "3650";
      anchorField.hidden = anchor.disabled = limits.strategy.value !== "month_rolling";
    }
    function validate() {
      sync();
      var message = "";
      if (mode.value === "fixed") message = expiry.date.validationMessage || expiry.time.validationMessage;
      if (!["never", "fixed", "first_use"].includes(mode.value)) message = M.t("lifecycle.invalid_expiry_mode");
      if (mode.value === "first_use" && !integer(duration, 3650, false)) message = M.t("lifecycle.invalid_activation_duration_days");
      if (!["off", "days", "months"].includes(renewal.value)) message = M.t("lifecycle.invalid_interval");
      if (renewal.value !== "off") {
        if (!integer(interval, renewal.value === "months" ? 120 : 3650, false)) message = M.t("lifecycle.invalid_interval");
        if (mode.value === "never") message = M.t("lifecycle.renewal_needs_expiry");
        if (capMode.value === "limited" && !integer(cap, 10000, false)) message = M.t("lifecycle.invalid_renewal_limit");
      }
      if (!anchor.disabled && !integer(anchor, 31, true)) message = M.t("lifecycle.invalid_traffic_reset_anchor_day");
      error.textContent = message;
      return !message;
    }
    function change() {
      if (!validate()) throw new Error(error.textContent);
      var body = {};
      if (mode.value === "first_use") {
        if (!user || initialMode !== "first_use" || duration.value !== initialDuration) {
          body.activation_duration_days = Number(duration.value); body.expire_at = null;
        }
      } else {
        Object.assign(body, expiry.change());
        if (!user || mode.value !== initialMode) {
          body.expire_at = mode.value === "never" ? null : expiry.value();
          if (user && initialMode === "first_use") body.activation_duration_days = null;
        }
      }
      var intervalChanged = renewal.value !== initialRenewal || interval.value !== initialInterval;
      var capChanged = capMode.value !== initialCapMode || cap.value !== initialCap;
      if (intervalChanged) {
        body.renewal_interval_days = renewal.value === "days" ? Number(interval.value) : null;
        body.renewal_interval_months = renewal.value === "months" ? Number(interval.value) : null;
      }
      if (intervalChanged && renewal.value === "off" || renewal.value !== "off" && capChanged) {
        body.renewal_limit = renewal.value === "off" || capMode.value === "unlimited" ? null : Number(cap.value);
      }
      if (!anchor.disabled && anchor.value !== initialAnchor) body.traffic_reset_anchor_day = anchor.value ? Number(anchor.value) : null;
      return body;
    }
    var timer, turn = 0, latest = M.latestOnly();
    function update() {
      var mine = ++turn;
      clearTimeout(timer);
      if (!validate()) return;
      timer = setTimeout(function () {
        if (!nodes.isConnected || mine !== turn) return;
        var body = change();
        if (user) body.username = user.username;
        body.traffic_limit_strategy = limits.strategy.value;
        latest(M.api("POST", "api/users/preview", body, { passive: true })).then(function (result) {
          if (nodes.isConnected && mine === turn) M.mount(preview, M.scheduleFacts(result));
        }, function (e) { if (nodes.isConnected && mine === turn) error.textContent = M.errorText(e); });
      }, 250);
    }
    mode.addEventListener("change", function () {
      expiry.never.checked = mode.value !== "fixed";
      expiry.never.dispatchEvent(new Event("change", { bubbles: true }));
      update();
    });
    expiry.never.addEventListener("change", function () {
      if (mode.value !== "first_use") mode.value = expiry.never.checked ? "never" : "fixed";
      sync();
    });
    // Typed dates, time edits, and the +1/+3 month presets (which dispatch input on the date) refresh the preview.
    [expiry.date, expiry.time].forEach(function (input) {
      input.addEventListener("input", function () {
        if (mode.value !== "first_use") mode.value = expiry.never.checked ? "never" : "fixed";
        update();
      });
    });
    [duration, renewal, interval, capMode, cap, anchor, limits.strategy].forEach(function (input) {
      input.addEventListener(input.tagName === "SELECT" ? "change" : "input", update);
    });
    validate();
    Promise.resolve().then(function () {
      if (mode.form) mode.form.addEventListener("submit", function (event) {
        if (validate()) return;
        event.preventDefault(); event.stopImmediatePropagation();
        error.scrollIntoView && error.scrollIntoView({ block: "nearest" });
      }, true);
    });
    return { nodes: nodes, change: change };
  };
})();
