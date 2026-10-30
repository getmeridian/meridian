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
  var latest = null;
  var notice = null;
  // Available before any catalog arrives; these two labels also live in all four catalogs.
  var BOOTSTRAP = {
    en: { text: "Language files could not be loaded. Retry from this server.", retry: "Try again" },
    ru: { text: "Не удалось загрузить файлы языка. Повторите загрузку с этого сервера.", retry: "Попробовать снова" },
    fa: { text: "فایل‌های زبان بارگیری نشدند. دوباره از همین سرور تلاش کنید.", retry: "تلاش دوباره" },
    zh: { text: "无法加载语言文件，请从此服务器重试。", retry: "重试" },
  };

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
    return M.rawApi("GET", "ui/locales/" + code + ".json", undefined, { passive: true }).then(function (catalog) {
      if (!catalog || Array.isArray(catalog) || typeof catalog !== "object" || !Object.keys(catalog).length) {
        throw new Error("catalog unavailable");
      }
      return catalog;
    });
  }

  function clearNotice() { if (notice) notice.remove(); notice = null; }

  function showNotice(code, retry) {
    clearNotice();
    var copy = BOOTSTRAP[code] || BOOTSTRAP.en;
    var button = M.h("button", { class: "btn btn-ghost btn-sm", type: "button", text: copy.retry,
      onclick: function () { button.disabled = true; retry(); } });
    notice = M.h("aside", { class: "i18n-notice notice notice-warn", role: "status", lang: code,
      dir: code === "fa" ? "rtl" : "ltr" }, M.h("span", { text: copy.text }), button);
    document.body.appendChild(notice);
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
      clearNotice();
      latest = latest || M.latestOnly();
      return new Promise(function (resolve) {
        function attempt() {
          var both = [fetchCatalog("en"), code === "en" ? Promise.resolve(null) : fetchCatalog(code)];
          return latest(Promise.allSettled(both)).then(function (catalogs) {
            var englishReady = catalogs[0].status === "fulfilled";
            var selectedReady = code !== "en" && catalogs[1].status === "fulfilled";
            if (!englishReady && !selectedReady) {
              showNotice(code, function () {
                attempt().then(function (ready) { if (ready && M.render) M.render(); });
              });
              // On first boot, keep the caller waiting until retry brings readable labels.
              if (Object.keys(current).length) resolve();
              return false;
            }
            if (englishReady) english = catalogs[0].value;
            current = selectedReady ? catalogs[1].value : english;
            apply(selectedReady ? code : "en");
            clearNotice();
            resolve();
            return true;
          });
        }
        attempt();
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
