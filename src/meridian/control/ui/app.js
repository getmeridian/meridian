/* Meridian console: start-up and the signed-in shell.
 * Boot order: the language, then a setup link's token (stripped from the URL at once),
 * then the sign-in state, then the screen the hash names. */
(function () {
  "use strict";
  var M = (window.M = window.M || {});
  var h = M.h;
  var root = null;
  var setupToken = "";
  var stopScreen = null;

  var SCREENS = [
    { name: "people", render: function (view, route) { return route.arg ? M.renderPerson(view, route.arg) : M.renderPeople(view); } },
    { name: "servers", render: function (view) { return M.renderServers(view); } },
    { name: "revisions", render: function (view, route) { return M.renderRevisions(view, route.arg); } },
    { name: "tokens", render: function (view) { return M.renderTokens(view); } },
    { name: "settings", render: function (view) { return M.renderSettings(view); } },
  ];

  function refreshAuth() {
    return M.rawApi("GET", "auth/state").then(function (auth) { M.state.auth = auth; return auth; });
  }

  function refreshFleet() {
    return M.api("GET", "ui/api/fleet").then(function (fleet) { M.state.fleet = fleet; return fleet; },
      function () { M.state.fleet = M.state.fleet || { declared_users: [], display_names: {}, servers: [] }; });
  }

  M.onSignedOut = function () {
    if (stopScreen) { stopScreen(); stopScreen = null; }
    M.state.auth = null;
    refreshAuth().then(M.render, showFatal);
  };

  function signOut() {
    M.rawApi("POST", "auth/logout", {}).finally(function () { M.onSignedOut(); });
  }

  /* Draw what the current state and hash call for. */
  M.render = function () {
    if (stopScreen) { stopScreen(); stopScreen = null; }
    var auth = M.state.auth;
    document.title = (M.state.fleet && M.state.fleet.profile_title) || "Meridian";
    if (!auth) return;
    if (!auth.configured) return M.renderNotice(root, "auth.unconfigured_title", "auth.unconfigured_text", "meridian apply");
    if (setupToken) return renderSetupLink();
    if (!auth.owner_exists) return M.renderNotice(root, "auth.no_owner_title", "auth.no_owner_text", "meridian panel");
    if (!auth.signed_in) return M.renderLogin(root, auth, afterSignIn);
    renderShell();
  };

  function afterSignIn() {
    refreshAuth().then(refreshFleet).then(M.render, showFatal);
  }

  function renderSetupLink() {
    var token = setupToken;
    M.mount(root, h("div", { class: "boot" }, h("span", { class: "spinner" })));
    M.rawApi("POST", "auth/setup/check", { token: token }).then(function (result) {
      M.renderSetup(root, token, result.purpose, function (purpose) {
        setupToken = "";
        refreshAuth().then(refreshFleet).then(function () {
          if (purpose === "setup" || !M.state.auth.totp_enabled) {
            M.renderTotpOffer(root, function () { M.go("people"); });
          } else {
            M.render();
          }
        }, showFatal);
      });
    }, function (error) {
      setupToken = "";
      if (error.code === "rate_limited") {
        M.renderNotice(root, "auth.link_title", "error.rate_limited_link", "");
      } else {
        M.renderNotice(root, "auth.link_title", "auth.link_text", "meridian panel --setup-link");
      }
    });
  }

  function renderShell() {
    var route = M.route();
    var screen = SCREENS.filter(function (s) { return s.name === route.name; })[0] || SCREENS[0];
    var view = h("main", { id: "view", class: "view", tabindex: "-1" });
    var nav = h("nav", { class: "tabs", "aria-label": M.t("nav.label") }, SCREENS.map(function (s) {
      return h("a", { href: "#/" + s.name, class: "tab" + (s === screen ? " active" : ""),
        "aria-current": s === screen ? "page" : null }, M.t("nav." + s.name));
    }));
    M.mount(root, [
      h("header", { class: "top" },
        h("div", { class: "top-inner" },
          h("a", { class: "top-brand", href: "#/people" }, h("img", { src: "ui/icon.svg", alt: "", class: "brand-mark" }),
            h("span", { class: "top-title", text: (M.state.fleet && M.state.fleet.profile_title) || "Meridian" })),
          h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: signOut }, M.icon("out"),
            h("span", { class: "hide-sm", text: M.t("auth.sign_out") }))),
        h("div", { class: "top-inner" }, nav)),
      view,
    ]);
    var cleanup = screen.render(view, route);
    stopScreen = typeof cleanup === "function" ? cleanup : null;
  }

  function showFatal(error) {
    M.mount(root, h("main", { class: "auth" }, h("div", { class: "auth-card" },
      h("h1", { text: M.t("error.fatal_title") }),
      h("p", { class: "muted", text: M.errorText(error) }),
      h("button", { class: "btn btn-primary", type: "button", onclick: function () { location.reload(); } },
        M.t("action.retry")))));
  }

  function start() {
    root = document.getElementById("app");
    root.classList.remove("boot");
    setupToken = M.takeSetupToken();
    window.addEventListener("hashchange", function () {
      if (M.state.auth && M.state.auth.signed_in) M.render();
    });
    M.i18n.load().then(refreshAuth).then(function (auth) {
      return auth.signed_in ? refreshFleet() : null;
    }).then(M.render, showFatal);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
