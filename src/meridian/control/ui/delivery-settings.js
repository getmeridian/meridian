/* Service-owned delivery settings; person editing stays in its own screen. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;
  var FIELDS = ["name", "expires", "days_left", "used", "quota"];

  M.languageSection = function () {
    var select = h("select", { disabled: true, "data-language": "default" },
      [["en", "English"], ["ru", "Русский"], ["fa", "فارسی"], ["zh", "中文"]].map(function (item) {
        return h("option", { value: item[0], lang: item[0], text: item[1] });
      }));
    var error = h("p", { class: "form-error", role: "alert" });
    var save = h("button", { type: "submit", class: "btn btn-primary btn-sm", disabled: true }, M.t("action.save"));
    var form = h("form", { class: "stack" }, M.field(M.t("language.default"), select), error, save);
    M.api("GET", "api/language").then(function (state) {
      select.value = state.language;
      select.disabled = save.disabled = false;
    }, function (e) { error.textContent = M.errorText(e); });
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var language = select.value;
      M.busy(form, function () { return M.api("PUT", "api/language", { language: language }); }).then(function () {
        M.toast(M.t("language.saved"));
      }, function () { /* shown by M.busy */ });
    });
    return h("section", { class: "card" }, h("h2", { text: M.t("language.title") }),
      h("p", { class: "muted small", text: M.t("language.hint") }), form);
  };

  function localTime(value) {
    if (!value) return "";
    var date = new Date(value);
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }

  function noticeError(text) {
    if (!text.trim() || Array.from(text).length > 500) return M.t("notice.length");
    if (/[\x00-\x1f\x7f\u2028\u2029\u202a-\u202e\u2066-\u2069]/.test(text)) return M.t("notice.paragraph");
    if (/[{}]/.test(text.replace(/\{(name|expires|days_left|used|quota)\}/g, ""))) return M.t("notice.allowed", { fields: FIELDS.map(function (key) { return "{" + key + "}"; }).join(", ") });
    return "";
  }

  M.noticeSection = function () {
    var text = h("textarea", { rows: "3", maxlength: "1000", dir: "auto", "data-notice": "text" });
    var until = h("input", { type: "datetime-local", "data-notice": "until" });
    var person = h("input", { type: "text", maxlength: "64", autocomplete: "off", "data-notice": "person" });
    var status = h("p", { class: "muted small" });
    var preview = h("p", { class: "link-text", dir: "auto", "data-notice": "preview", "aria-live": "polite" });
    var error = h("p", { class: "form-error", role: "alert" });
    var clear = h("button", { type: "button", class: "btn btn-ghost btn-sm", disabled: true }, M.t("notice.clear"));
    var check = h("button", { type: "button", class: "btn btn-ghost btn-sm", disabled: true }, M.t("notice.preview"));
    var save = h("button", { type: "submit", class: "btn btn-primary btn-sm", disabled: true }, M.t("action.save"));
    var form = h("form", { class: "stack", novalidate: true },
      M.field(M.t("notice.text"), text, M.t("notice.text_hint")),
      M.field(M.t("notice.until"), until, M.t("notice.until_hint")),
      M.field(M.t("notice.person"), person, M.t("notice.person_hint")),
      status, h("p", { class: "label", text: M.t("notice.preview") }), preview, error,
      h("div", { class: "row-actions" }, check, clear, save));
    var section = h("section", { class: "card" }, h("h2", { text: M.t("notice.title") }),
      h("p", { class: "muted small", text: M.t("notice.delivery") }), form);

    function load() {
      M.api("GET", "api/notice").then(function (state) {
        text.value = state.notice ? state.notice.text : "";
        until.value = state.notice ? localTime(state.notice.until) : "";
        preview.textContent = state.preview || M.t("notice.none");
        status.textContent = state.notice && !state.active ? M.t("notice.expired") : "";
        clear.disabled = check.disabled = save.disabled = false;
      }, function (e) {
        error.textContent = M.errorText(e);
        M.mount(status, h("button", { type: "button", class: "btn btn-ghost btn-sm", onclick: load }, M.t("action.retry")));
      });
    }
    function request(clearing) {
      var instant = until.value ? new Date(until.value) : null;
      var problem = clearing ? "" : noticeError(text.value);
      if (!clearing && until.value && (!isFinite(instant.getTime()) || instant.getTime() <= Date.now())) problem = M.t("notice.until_invalid");
      if (problem) { error.textContent = problem; return null; }
      return { notice: clearing ? null : { text: text.value, until: instant ? instant.toISOString() : null },
        for_user: clearing ? null : person.value.trim() || null, confirm: false };
    }
    function run(clearing, saving) {
      var body = request(clearing);
      if (!body) return;
      M.busy(form, function () {
        return M.api("PUT", "api/notice", body).then(function (state) {
          preview.textContent = state.preview || M.t("notice.none");
          if (!saving) return null;
          return M.confirm(M.t(clearing ? "notice.clear" : "notice.confirm"),
            state.preview || M.t("notice.none"),
            M.t(clearing ? "notice.clear" : "action.save"), clearing).then(function (ok) {
              if (!ok) return null;
              body.confirm = true;
              return M.api("PUT", "api/notice", body);
            });
        });
      }).then(function (state) {
        if (!state) return;
        if (clearing) text.value = until.value = "";
        status.textContent = "";
        M.toast(M.t("notice.saved"));
      }, function () { /* M.busy explains the failure in the form. */ });
    }
    check.addEventListener("click", function () { run(false, false); });
    clear.addEventListener("click", function () { run(true, true); });
    form.addEventListener("submit", function (event) { event.preventDefault(); run(false, true); });
    load();
    return section;
  };
})();
