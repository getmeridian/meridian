/* Exact browser-local date/time edits; calendar presets come from core through a read-only preview. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;
  var NEVER_EXPIRES_YEAR = 2099; // core.client_limits.NEVER_EXPIRES_YEAR
  function pad(value) { return String(value).padStart(2, "0"); }

  M.dateValue = function (timestamp) {
    if (!timestamp) return "";
    var d = new Date(timestamp);
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  };
  function timeValue(timestamp) {
    if (!timestamp) return "23:59:59";
    var d = new Date(timestamp);
    return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }

  M.expiryFields = function (timestamp, username) {
    var initial = timestamp || null;
    var date = h("input", { type: "date", max: NEVER_EXPIRES_YEAR + "-01-01", value: M.dateValue(initial) });
    var time = h("input", { type: "time", step: "1", value: timeValue(initial) });
    var never = h("input", { type: "checkbox", checked: !initial });
    var initialDate = date.value, initialTime = time.value, initialNever = never.checked;
    var error = h("p", { class: "field-error", role: "alert" });
    var utc = h("code", { class: "expiry-utc", dir: "ltr" });
    var zone = new Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    var presets = h("div", { class: "row-actions" });
    var nodes = h("fieldset", { class: "expiry-fields stack-sm" },
      h("legend", { text: M.t("people.expiry") }),
      h("label", { class: "expiry-never" }, never, M.t("people.no_expiry")),
      h("div", { class: "grid-2" }, M.field(M.t("person.expiry_date"), date), M.field(M.t("person.expiry_time"), time)),
      h("p", { class: "hint expiry-zone", text: M.t("person.expiry_zone", { zone: "\u2068" + zone + "\u2069" }) }),
      h("p", { class: "hint" }, M.t("person.expiry_utc"), " ", utc),
      error, username ? presets : null);
    var turn = 0;
    function edited() { return never.checked !== initialNever || date.value !== initialDate || time.value !== initialTime; }
    function moment() {
      if (never.checked || !date.value || !time.value) return null;
      var normalizedTime = /^\d\d:\d\d$/.test(time.value) ? time.value + ":00" : time.value;
      var d = new Date(date.value + "T" + normalizedTime);
      if (!Number.isFinite(d.getTime()) || M.dateValue(d) !== date.value || timeValue(d) !== normalizedTime) return null;
      return d;
    }
    function value() {
      if (!edited()) return initial;
      var d = moment();
      return never.checked ? null : d ? d.toISOString().replace(/\.\d{3}Z$/, "Z") : null;
    }
    function validate() {
      date.disabled = time.disabled = never.checked;
      var d = moment(), message = "", target = date;
      if (!never.checked) {
        if (!date.value || !d) { message = M.t("person.expiry_invalid", { choice: M.t("people.no_expiry") }); if (date.value) target = time; }
        if (d && d.getUTCFullYear() >= NEVER_EXPIRES_YEAR) message = M.t("people.expiry_too_far");
        else if (d && edited() && d.getTime() <= Date.now()) message = M.t("person.expiry_past", { choice: M.t("people.no_expiry") });
      }
      date.setCustomValidity(target === date ? message : "");
      time.setCustomValidity(target === time ? message : "");
      date.setAttribute("aria-invalid", date.validationMessage ? "true" : "false");
      time.setAttribute("aria-invalid", time.validationMessage ? "true" : "false");
      error.textContent = message;
      utc.textContent = never.checked ? M.t("people.no_expiry") : value() || "—";
      return !message;
    }
    [date, time, never].forEach(function (input) {
      input.addEventListener(input === never ? "change" : "input", function () {
        turn++;
        if (input === date && date.value) never.checked = false;
        validate();
      });
    });
    if (username) M.mount(presets, [1, 3].map(function (months) {
      return h("button", { class: "btn btn-ghost btn-sm", type: "button", dataset: { months: String(months) },
        onclick: function () {
          var mine = ++turn;
          M.busy(presets, function () {
            return M.api("GET", "ui/api/people/" + encodeURIComponent(username) + "/expiry?months=" + months);
          }).then(function (preview) {
            if (!nodes.isConnected || turn !== mine) return;
            date.value = M.dateValue(preview.expire_at); time.value = timeValue(preview.expire_at); never.checked = false;
            date.dispatchEvent(new Event("input", { bubbles: true }));
          }, function () { /* M.busy shows the preview error. */ });
        } }, M.t(months === 1 ? "person.plus_month" : "person.plus_three_months"));
    }));
    validate();
    Promise.resolve().then(function () {
      if (date.form) date.form.addEventListener("submit", function (event) {
        if (validate()) return;
        event.preventDefault(); event.stopImmediatePropagation();
        var invalid = date.validationMessage ? date : time;
        invalid.focus(); invalid.reportValidity();
      }, true);
    });
    return { nodes: nodes, date: date, time: time, never: never, value: value, validate: validate,
      change: function () { var chosen = value(); return chosen === initial ? {} : { expire_at: chosen }; } };
  };
})();
