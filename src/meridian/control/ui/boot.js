/* Meridian console: runs before first paint, so the saved theme and language never flash.
 * Loaded without defer from <head>; everything else waits for the DOM. */
(function () {
  "use strict";
  var root = document.documentElement;
  var theme = null;
  var lang = null;
  try {
    theme = localStorage.getItem("mrd.theme");
    lang = localStorage.getItem("mrd.lang");
  } catch (e) { /* storage disabled: follow the system */ }
  if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
  if (lang === "en" || lang === "ru" || lang === "fa" || lang === "zh") {
    root.lang = lang;
    root.dir = lang === "fa" ? "rtl" : "ltr";
  }
})();
