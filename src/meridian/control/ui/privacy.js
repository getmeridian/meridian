/* Meridian console: control-service privacy choices, disclosures, and their audit trail. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;

  function isolated(value) { return "\u2068" + M.count(value) + "\u2069"; }

  M.privacySection = function () {
    var section = h("section", { class: "stack privacy-section" }, h("span", { class: "spinner" }));
    M.api("GET", "api/privacy").then(function (state) { draw(section, state); }, function (e) {
      M.mount(section, h("p", { class: "form-error", role: "alert", text: M.errorText(e) }));
    });
    return section;
  };

  function choices(name, values, current, disclosures, update) {
    return h("div", { class: "stack-sm" }, values.map(function (value) {
      var input = h("input", { type: "radio", name: name, value: value, checked: value === current });
      input.addEventListener("change", update);
      return h("label", { class: "privacy-choice" }, input,
        h("span", { class: "stack-sm" }, h("span", { class: "strong", text: M.t("privacy.level_" + value) }),
          h("span", { class: "muted small", text: disclosures[value] }),
          value === "private" ? h("span", { class: "muted small", text: M.t("privacy.restore_private") }) : null));
    }));
  }

  function draw(section, state) {
    var before = state.settings;
    var cap = h("input", { type: "number", min: "1", max: "100", step: "1", value: String(before.default_device_limit) });
    var days = h("input", { type: "number", min: "7", max: "730", step: "1", value: String(before.history_days) });
    var deviceChoices = choices("device_limits", ["off", "private", "full"], before.device_limits, {
      off: M.t("privacy.devices_off"), private: M.t("privacy.devices_private"), full: M.t("privacy.devices_full"),
    }, update);
    var historyChoices = choices("usage_history", ["off", "daily"], before.usage_history, {
      off: M.t("privacy.history_off"), daily: M.t("privacy.history_daily", { days: isolated(before.history_days) }),
    }, update);
    var dailyText = historyChoices.querySelector('input[value="daily"]').parentNode.querySelector(".muted");
    var error = h("p", { class: "form-error", role: "alert" });
    var form = h("form", { class: "stack", novalidate: true },
      h("div", { class: "cards" },
        h("fieldset", { class: "card privacy-options" }, h("legend", { text: M.t("privacy.devices") }), deviceChoices,
          M.field(M.t("privacy.device_limit"), cap, M.t("privacy.device_limit_hint"))),
        h("fieldset", { class: "card privacy-options" }, h("legend", { text: M.t("privacy.history") }), historyChoices,
          M.field(M.t("privacy.history_days"), days, M.t("privacy.history_days_hint")))),
      error,
      h("div", { class: "row-actions" }, h("button", { class: "btn btn-primary btn-sm", type: "submit" }, M.t("action.save"))));
    function picked(choices) { return choices.querySelector("input:checked").value; }
    function update() {
      cap.disabled = picked(deviceChoices) === "off";
      days.disabled = picked(historyChoices) === "off";
      if (cap.disabled) cap.value = String(before.default_device_limit);
      if (days.disabled) days.value = String(before.history_days);
      var n = Number(days.value);
      dailyText.textContent = M.t("privacy.history_daily", {
        days: isolated(Number.isInteger(n) && n >= 7 && n <= 730 ? n : before.history_days),
      });
    }
    days.addEventListener("input", update);
    update();
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var after = {
        device_limits: picked(deviceChoices), default_device_limit: Number(cap.value),
        usage_history: picked(historyChoices), history_days: Number(days.value),
      };
      if (!Number.isInteger(after.default_device_limit) || after.default_device_limit < 1 || after.default_device_limit > 100 ||
          !Number.isInteger(after.history_days) || after.history_days < 7 || after.history_days > 730) {
        error.textContent = M.t("privacy.number_range");
        return;
      }
      if (same(before, after)) { M.toast(M.t("privacy.no_changes")); return; }
      M.busy(form, function () {
        return confirmPrivacy(before, after).then(function (ok) {
          if (!ok) throw new M.ApiError(0, "cancelled", "");
          return M.api("PUT", "api/privacy", after);
        });
      }).then(function (result) {
        M.toast(M.t(result.changed ? "privacy.saved" : "privacy.no_changes"));
        draw(section, result);
      }, function () { /* a cancelled confirmation or step-up leaves the form intact */ });
    });
    var deleted = state.deleted || {};
    M.mount(section, [
      h("h2", { class: "section-title", text: M.t("privacy.title") }),
      h("p", { class: "muted", text: M.t("privacy.intro") }),
      h("p", { class: "muted small", text: M.t("privacy.no_addresses") }), form,
      deleted.devices || deleted.traffic_daily || deleted.node_traffic_daily
        ? h("p", { class: "notice", role: "status", text: M.t("privacy.deleted", {
          devices: isolated(deleted.devices || 0), users: isolated(deleted.traffic_daily || 0), nodes: isolated(deleted.node_traffic_daily || 0),
        }) }) : null,
      state.rekeyed_devices ? h("p", { class: "notice", role: "status",
        text: M.t("privacy.rekeyed", { count: isolated(state.rekeyed_devices) }) }) : null,
      changes(state.changes || []),
    ]);
  }

  function same(a, b) {
    return a.device_limits === b.device_limits && a.default_device_limit === b.default_device_limit &&
      a.usage_history === b.usage_history && a.history_days === b.history_days;
  }

  function summary(settings) {
    return h("span", { class: "stack-sm" },
      h("span", {}, M.t("privacy.devices"), ": ", M.t("privacy.level_" + settings.device_limits),
        " · ", M.t("privacy.device_limit"), ": ", h("bdi", { text: M.count(settings.default_device_limit) })),
      h("span", {}, M.t("privacy.history"), ": ", M.t("privacy.level_" + settings.usage_history),
        " · ", M.t("privacy.history_days"), ": ", h("bdi", { text: M.count(settings.history_days) })));
  }

  function confirmPrivacy(before, after) {
    var disclosures = [];
    if (after.device_limits === "private") disclosures.push(M.t("privacy.devices_private"), M.t("privacy.restore_private"));
    if (after.device_limits === "full") disclosures.push(M.t("privacy.devices_full"));
    if (after.usage_history === "daily") disclosures.push(M.t("privacy.history_daily", { days: isolated(after.history_days) }));
    var removal = [];
    if (before.device_limits !== "off" && after.device_limits === "off") removal.push(M.t("privacy.delete_devices"));
    if (before.device_limits === "full" && after.device_limits === "private") removal.push(M.t("privacy.rekey_devices"));
    if (before.device_limits === "private" && after.device_limits === "full") removal.push(M.t("privacy.reset_private"));
    if (before.usage_history === "daily" && after.usage_history === "off") removal.push(M.t("privacy.delete_history"));
    if (before.usage_history === "daily" && after.usage_history === "daily" && after.history_days < before.history_days) {
      removal.push(M.t("privacy.prune_history", { days: isolated(after.history_days) }));
    }
    return M.dialog(M.t("privacy.confirm_title"), function (close) {
      return [
        summary(after),
        disclosures.map(function (text) { return h("p", { class: "muted", text: text }); }),
        removal.map(function (text) { return h("p", { class: "notice notice-warn", text: text }); }),
        h("p", { class: "muted small", text: M.t("privacy.step_up") }),
        h("div", { class: "actions" },
          h("button", { class: "btn btn-ghost", type: "button", onclick: function () { close(false); } }, M.t("action.cancel")),
          h("button", { class: "btn " + (removal.length ? "btn-danger" : "btn-primary"), type: "button",
            onclick: function () { close(true); } }, M.t("action.save"))),
      ];
    }).then(Boolean);
  }

  function changes(items) {
    return h("section", { class: "card" }, h("h3", { text: M.t("privacy.changes") }),
      !items.length ? h("p", { class: "muted small", text: M.t("privacy.no_changes_yet") })
        : h("div", { class: "list list-plain" }, items.map(function (change) {
          return h("div", { class: "privacy-change" },
            h("p", { class: "small muted" }, h("bdi", { text: change.author }), " · ", M.date(change.changed_at, true)),
            h("div", { class: "privacy-diff" },
              h("div", { class: "stack-sm" }, h("p", { class: "label", text: M.t("privacy.before") }), summary(change.before)),
              h("div", { class: "stack-sm" }, h("p", { class: "label", text: M.t("privacy.after") }), summary(change.after))));
        })));
  }
})();
