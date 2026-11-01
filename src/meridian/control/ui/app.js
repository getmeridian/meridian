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
  var activeHash = null, position = 0, navigationTurn = 0, ignoreHash = null;
  var restoring = null, allowedTraversal = false;

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
    M.tryLeave().then(function (leave) {
      if (leave) M.rawApi("POST", "auth/logout", {}).finally(function () { M.onSignedOut(); });
    });
  }

  /* Draw what the current state and hash call for. */
  M.render = function (confirmed) {
    if (confirmed !== true && M.state.auth && M.state.auth.signed_in && M.hasUnsavedEdits()) {
      return M.tryLeave().then(function (leave) { if (leave) renderNow(); });
    }
    renderNow();
  };

  function renderNow() {
    if (stopScreen) { stopScreen(); stopScreen = null; }
    M.discardDrafts(root);
    var auth = M.state.auth;
    document.title = (M.state.fleet && M.state.fleet.profile_title) || "Meridian";
    if (!auth) return;
    if (!auth.configured) return M.renderNotice(root, "auth.unconfigured_title", "auth.unconfigured_text", "meridian apply");
    if (setupToken) return renderSetupLink();
    if (!auth.owner_exists) return M.renderNotice(root, "auth.no_owner_title", "auth.no_owner_text", "meridian panel");
    if (!auth.signed_in) return M.renderLogin(root, auth, afterSignIn);
    renderShell();
  }

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
    if (activeHash === null) remember(location.hash, 0);
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
          h("button", { class: "btn btn-ghost btn-sm", type: "button", "aria-label": M.t("auth.sign_out"),
            onclick: signOut }, M.icon("out"),
            h("span", { class: "hide-sm", text: M.t("auth.sign_out") }))),
        h("div", { class: "top-inner" }, nav)),
      view,
    ]);
    var cleanup = screen.render(view, route);
    stopScreen = typeof cleanup === "function" ? cleanup : null;
  }

  function remember(hash, nextPosition) {
    position = nextPosition;
    activeHash = hash;
    history.replaceState({ meridianPosition: position }, "", location.pathname + location.search + hash);
  }

  M.replaceRouteHash = function (hash) {
    remember(hash, position);
  };

  M.navigate = function (hash) {
    var mine = ++navigationTurn;
    return M.tryLeave().then(function (leave) {
      if (!leave || mine !== navigationTurn) return;
      if (activeHash === null) remember(location.hash, 0);
      if (hash !== activeHash) {
        history.pushState({ meridianPosition: position + 1 }, "", location.pathname + location.search + hash);
        position++;
        activeHash = hash;
      }
      renderNow();
    });
  };

  function traversed(event) {
    if (!M.state.auth || !M.state.auth.signed_in) return;
    ignoreHash = location.hash;
    if (restoring) {
      var target = restoring;
      restoring = null;
      remember(activeHash, position);
      M.tryLeave().then(function (leave) {
        if (!leave || target.turn !== navigationTurn) return;
        allowedTraversal = true;
        history.go(target.position - position);
      });
      return;
    }
    var nextPosition = event.state && Number.isInteger(event.state.meridianPosition)
      ? event.state.meridianPosition : position + 1;
    if (!allowedTraversal && M.hasUnsavedEdits()) {
      restoring = { hash: location.hash, position: nextPosition, turn: ++navigationTurn };
      history.go(position - nextPosition);
      return;
    }
    allowedTraversal = false;
    remember(location.hash, nextPosition);
    renderNow();
  }

  function hashChanged() {
    if (!M.state.auth || !M.state.auth.signed_in) return;
    if (ignoreHash === location.hash) { ignoreHash = null; return; }
    ignoreHash = null;
    if (location.hash === activeHash) return;
    if (M.hasUnsavedEdits()) {
      restoring = { hash: location.hash, position: position + 1, turn: ++navigationTurn };
      history.back();
    } else {
      remember(location.hash, position + 1);
      renderNow();
    }
  }

  function routeClick(event) {
    var link = event.target.closest && event.target.closest("a");
    var hash = link && link.getAttribute("href");
    if (!hash || hash.charAt(0) !== "#" || event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    if (!M.state.auth || !M.state.auth.signed_in) return;
    event.preventDefault();
    M.navigate(hash);
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
    window.addEventListener("hashchange", hashChanged);
    window.addEventListener("popstate", traversed);
    document.addEventListener("click", routeClick);
    M.i18n.load().then(refreshAuth).then(function (auth) {
      return auth.signed_in ? refreshFleet() : null;
    }).then(M.render, showFatal);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
