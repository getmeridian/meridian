/* Meridian console: language.
 * Flat JSON catalogs per locale (ui/locales/<code>.json) with {name} placeholders;
 * English is the source and the fallback for a missing key. Explicit beats guessed:
 * the saved choice, then the browser's languages, then English. Farsi is RTL. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var LANGS = ["en", "ru", "fa", "zh"];
  var NAMES = { en: "English", ru: "Русский", fa: "فارسی", zh: "中文" };
  var english = {};
  var current = {};
  var lang = "en";

  function detect() {
    var saved = null;
    try { saved = localStorage.getItem("mrd.lang"); } catch (e) { /* storage disabled */ }
    if (LANGS.indexOf(saved) >= 0) return saved;
    var wanted = navigator.languages || [navigator.language || "en"];
    for (var i = 0; i < wanted.length; i++) {
      var code = String(wanted[i]).toLowerCase().split("-")[0];
      if (LANGS.indexOf(code) >= 0) return code;
    }
    return "en";
  }

  function fetchCatalog(code) {
    return fetch("ui/locales/" + code + ".json", { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw new Error("catalog " + code + ": HTTP " + r.status);
      return r.json();
    });
  }

  function apply(code) {
    lang = code;
    var root = document.documentElement;
    root.lang = code;
    root.dir = code === "fa" ? "rtl" : "ltr";
  }

  M.i18n = {
    langs: LANGS,
    names: NAMES,
    get lang() { return lang; },
    load: function (code) {
      code = LANGS.indexOf(code) >= 0 ? code : detect();
      var both = [fetchCatalog("en"), code === "en" ? Promise.resolve(null) : fetchCatalog(code)];
      return Promise.all(both).then(function (catalogs) {
        english = catalogs[0] || {};
        current = catalogs[1] || english;
        apply(code);
      }, function () {
        current = english;
        apply("en");
      });
    },
    choose: function (code) {
      try { localStorage.setItem("mrd.lang", code); } catch (e) { /* storage disabled */ }
      return M.i18n.load(code);
    },
    locale: function () { return lang === "zh" ? "zh-CN" : lang; },
  };

  /* Translate `key`, filling {name} placeholders from `vars`. Missing keys fall back to
   * English, then to the key itself (a visible bug rather than an empty label). */
  M.t = function (key, vars) {
    var text = Object.prototype.hasOwnProperty.call(current, key) ? current[key] : english[key];
    if (typeof text !== "string") text = key;
    if (vars) {
      text = text.replace(/\{(\w+)\}/g, function (whole, name) {
        return Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : whole;
      });
    }
    return text;
  };
})();
