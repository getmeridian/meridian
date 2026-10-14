// Studio's early theme: a classic external script (not inline), so it runs
// before first paint under the Engine's CSP `script-src 'self'`.
(function () {
  try {
    var theme = localStorage.getItem("starlight-theme") || localStorage.getItem("meridian-theme");
    if (theme === "dark" || theme === "light") document.documentElement.dataset.theme = theme;
  } catch (_error) {
    // Storage blocked: follow the OS preference.
  }
})();
