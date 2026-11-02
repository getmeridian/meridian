/* Browser-local display modes and account preferences; they never write service data. */
(function () {
  "use strict";
  var M = window.M, h = M.h;
  var value = "simple", turn = 0;
  try { if (localStorage.getItem("mrd.mode") === "detailed") value = "detailed"; } catch (e) { /* this tab only */ }
  document.documentElement.setAttribute("data-mode", value);

  M.mode = function () { return value; };
  M.isDetailedRoute = function (name) {
    return (M.consoleScreens || []).some(function (tab) { return tab.name === name && tab.detailed; });
  };

  M.applyMode = function (root) {
    var detailed = value === "detailed";
    root.querySelectorAll("[data-detailed-only]").forEach(function (node) { node.hidden = !detailed; });
    root.querySelectorAll("[data-mode-section]").forEach(function (section) {
      // Keep an edited form visible and mounted when reducing the amount of detail.
      section.open = detailed || M.hasUnsavedEdits(section);
      section.querySelector(".mode-caption").hidden = detailed;
    });
    if (M.applyServerMode) M.applyServerMode(root);
  };

  M.chooseMode = function (next) {
    var mine = ++turn;
    if ((next !== "simple" && next !== "detailed") || next === value) return Promise.resolve(false);
    function accept() {
      if (mine !== turn) return false;
      value = next;
      try { localStorage.setItem("mrd.mode", value); } catch (e) { /* this tab only */ }
      document.documentElement.setAttribute("data-mode", value);
      M.applyMode(document);
      window.dispatchEvent(new CustomEvent("meridian:mode", { detail: { mode: value } }));
      return true;
    }
    if (next === "simple" && M.isDetailedRoute(M.route().name) && M.navigate) {
      return M.navigate("#/overview").then(function () { return M.route().name === "overview" ? accept() : false; });
    }
    return Promise.resolve(accept());
  };

  M.modeNote = function () {
    return h("aside", { class: "mode-note", hidden: true, role: "note" },
      h("span", { class: "mode-note-text muted small", text: M.t("mode.shown_detailed") }),
      h("button", { class: "btn btn-soft btn-sm", type: "button", onclick: function () { M.chooseMode("detailed"); } },
        M.t("mode.switch_detailed")));
  };

  function themeValue() {
    var theme = document.documentElement.getAttribute("data-theme");
    return theme === "dark" || theme === "light" ? theme : "auto";
  }

  M.shellHeader = function (screen, signOut) {
    var modes = M.segmented(M.t("mode.label"), [
      { value: "simple", label: M.t("mode.simple") }, { value: "detailed", label: M.t("mode.detailed") },
    ], value, M.chooseMode, "mode-switch", "data-mode-choice");
    var themes = M.segmented(M.t("settings.theme"), ["auto", "light", "dark"].map(function (theme) {
      return { value: theme, label: M.t("theme." + theme) };
    }), themeValue(), function (theme) {
      try {
        if (theme === "auto") localStorage.removeItem("mrd.theme");
        else localStorage.setItem("mrd.theme", theme);
      } catch (e) { /* this tab only */ }
      if (theme === "auto") document.documentElement.removeAttribute("data-theme");
      else document.documentElement.setAttribute("data-theme", theme);
      themes.update(theme);
    }, "theme-switch", "data-theme-choice");
    var language = h("select", { "data-console-language": "" }, M.i18n.langs.map(function (code) {
      return h("option", { value: code, lang: code, selected: code === M.i18n.lang, text: M.i18n.names[code] });
    }));
    language.addEventListener("change", function () {
      var chosen = language.value;
      language.disabled = true;
      M.tryLeave().then(function (leave) {
        if (leave) return M.i18n.choose(chosen).then(function () { M.render(true); });
      }).finally(function () { language.value = M.i18n.lang; language.disabled = false; });
    });
    var toggle = h("summary", { class: "account-toggle", "aria-label": M.t("shell.account") },
      M.icon("shield"), h("span", { text: M.t("shell.account") }));
    var account = h("details", { class: "account-menu" }, toggle,
      h("div", { class: "account-panel card" },
        h("p", { class: "label", text: M.t("settings.theme") }), themes.node,
        M.field(M.t("settings.language"), language),
        h("button", { class: "btn btn-ghost", type: "button", "data-sign-out": "", "aria-label": M.t("auth.sign_out"), onclick: signOut },
          M.icon("out"), M.t("auth.sign_out"))));
    function outside(event) { if (!account.contains(event.target)) account.open = false; }
    function escape(event) {
      if (event.key === "Escape" && account.open) { account.open = false; toggle.focus(); }
    }
    document.addEventListener("click", outside);
    document.addEventListener("keydown", escape);
    var title = h("bdi", { class: "top-title" });
    var separator = h("span", { class: "brand-separator muted", "aria-hidden": "true", text: "·" });
    var nav = h("nav", { class: "tabs", "aria-label": M.t("nav.label") });
    var header = h("header", { class: "top" },
      h("div", { class: "top-inner shell-top" },
        h("a", { class: "top-brand", href: "#/overview" },
          h("img", { src: "ui/icon.svg", alt: "", class: "brand-mark" }),
          h("span", { class: "brand-name", text: "Meridian" }), separator, title),
        h("div", { class: "shell-controls" }, modes.node, account)),
      h("div", { class: "top-inner" }, nav));
    function updateTitle() {
      var fleetTitle = M.state.fleet && M.state.fleet.profile_title || "Meridian";
      title.textContent = fleetTitle;
      title.title = fleetTitle;
      title.hidden = separator.hidden = fleetTitle === "Meridian";
      document.title = fleetTitle;
    }
    function update() {
      modes.update(value);
      M.mount(nav, (M.consoleScreens || []).filter(function (tab) { return !tab.detailed || value === "detailed"; }).map(function (tab) {
        return h("a", { href: "#/" + tab.name, class: "tab" + (tab.name === screen ? " active" : ""),
          "aria-current": tab.name === screen ? "page" : null }, M.t("nav." + tab.name));
      }));
      updateTitle();
    }
    M.updateFleetTitle = updateTitle;
    update();
    return { node: header, update: update, stop: function () {
      document.removeEventListener("click", outside);
      document.removeEventListener("keydown", escape);
      if (M.updateFleetTitle === updateTitle) M.updateFleetTitle = null;
    } };
  };
})();
