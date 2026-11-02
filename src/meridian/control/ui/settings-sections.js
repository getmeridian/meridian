/* Settings sections share the person tabs' ARIA, keyboard, and guarded fragment pattern. */
(function () {
  "use strict";
  var M = window.M, h = M.h, sequence = 0;
  var choices = [
    { id: "general", label: "settings.general" }, { id: "notice", label: "settings.notice" },
    { id: "language", label: "settings.language" }, { id: "privacy", label: "privacy.title" },
    { id: "security", label: "settings.security" }, { id: "advanced", label: "settings.advanced" },
  ];

  M.settingsSection = function (value) {
    return choices.some(function (choice) { return choice.id === value; }) ? value : "general";
  };
  function hash(value) { return "#/settings" + (value === "general" ? "" : "/" + value); }

  M.settingsSections = function (changed) {
    var selected = null, prefix = "settings-" + (++sequence), panels = {}, tabs = [];
    var phone = window.matchMedia && window.matchMedia("(max-width: 640px)");
    choices.forEach(function (choice) {
      var id = prefix + "-" + choice.id;
      panels[choice.id] = h("section", { role: "tabpanel", id: id + "-panel", "aria-labelledby": id,
        "data-settings-panel": choice.id, tabindex: "0", hidden: true, class: "settings-panel stack" });
      tabs.push(h("a", { role: "tab", id: id, href: hash(choice.id), class: "settings-section",
        "aria-controls": panels[choice.id].id, "aria-selected": "false", tabindex: "-1",
        "data-settings-section": choice.id, text: M.t(choice.label), onclick: function (event) {
          if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
          event.preventDefault(); event.stopPropagation(); request(choice.id, true);
        } }));
    });
    var list = h("nav", { role: "tablist", "aria-label": M.t("settings.sections"), class: "settings-sections" }, tabs);
    var node = h("div", { class: "settings-layout" }, list,
      h("div", { class: "settings-panels" }, choices.map(function (choice) { return panels[choice.id]; })));
    function orientation() { list.setAttribute("aria-orientation", phone && phone.matches ? "horizontal" : "vertical"); }
    function mode() {
      tabs.forEach(function (tab, index) {
        tab.hidden = choices[index].id === "advanced" && M.mode() !== "detailed" && selected !== "advanced";
      });
    }
    function select(value) {
      var next = M.settingsSection(value), previous = selected;
      if (next !== previous) {
        changed(next, previous, panels[next], previous && panels[previous]); selected = next;
      }
      tabs.forEach(function (tab, index) {
        var active = choices[index].id === selected;
        tab.setAttribute("aria-selected", String(active)); tab.tabIndex = active ? 0 : -1;
        panels[choices[index].id].hidden = !active;
      });
      mode();
    }
    function request(value, focus) {
      var next = M.settingsSection(value);
      var work = selected === next ? Promise.resolve() : M.navigate ? M.navigate(hash(next)) : M.tryLeave(node).then(function (leave) {
        if (leave) { select(next); location.hash = hash(next); }
      });
      return Promise.resolve(work).then(function () {
        if (selected !== next || !node.isConnected) return false;
        if (focus) {
          var tab = tabs.filter(function (item) { return item.dataset.settingsSection === next; })[0];
          tab.focus({ preventScroll: true });
          if (tab.scrollIntoView) tab.scrollIntoView({ block: "nearest", inline: "nearest" });
        }
        return true;
      });
    }
    list.addEventListener("keydown", function (event) {
      var visible = tabs.filter(function (tab) { return !tab.hidden; });
      var index = visible.indexOf(event.target), next = index, rtl = document.documentElement.dir === "rtl";
      if (index < 0) return;
      if (event.key === "ArrowRight") next += rtl ? -1 : 1;
      else if (event.key === "ArrowLeft") next += rtl ? 1 : -1;
      else if (event.key === "ArrowDown") next++;
      else if (event.key === "ArrowUp") next--;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = visible.length - 1;
      else return;
      event.preventDefault(); request(visible[(next + visible.length) % visible.length].dataset.settingsSection, true);
    });
    orientation();
    if (phone && phone.addEventListener) phone.addEventListener("change", orientation);
    window.addEventListener("meridian:mode", mode);
    return { node: node, select: select, stop: function () {
      if (phone && phone.removeEventListener) phone.removeEventListener("change", orientation);
      window.removeEventListener("meridian:mode", mode);
    } };
  };
})();
