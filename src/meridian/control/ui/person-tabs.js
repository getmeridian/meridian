/* Person tabs share fragment navigation and the console's draft guard. */
(function () {
  "use strict";
  var M = window.M, h = M.h, sequence = 0;
  var choices = [
    { id: "access", label: "person.tab_access" }, { id: "usage", label: "person.tab_usage" },
    { id: "devices", label: "person.tab_devices" }, { id: "routing", label: "person.tab_routing" },
    { id: "keys", label: "person.tab_keys" },
  ];

  M.personTab = function (value) {
    return choices.some(function (choice) { return choice.id === value; }) ? value : "access";
  };
  M.personHash = function (username, tab) {
    return "#/people/" + encodeURIComponent(username) + (tab && tab !== "access" ? "/" + tab : "");
  };
  M.isDetailedPersonRoute = function (route) {
    return route.name === "people" && !!route.arg && M.personTab(route.extra) !== "access";
  };

  M.personTabs = function (username, initial, changed) {
    var selected = null, prefix = "person-" + (++sequence), panels = {}, buttons = [];
    var note = M.modeNote(); note.classList.add("person-mode-note");
    choices.forEach(function (choice) {
      var id = prefix + "-" + choice.id;
      panels[choice.id] = h("section", { role: "tabpanel", id: id + "-panel", "aria-labelledby": id,
        "data-person-panel": choice.id, tabindex: "0", hidden: true, class: "person-panel stack" });
      buttons.push(h("a", { role: "tab", id: id, href: M.personHash(username, choice.id),
        "aria-controls": panels[choice.id].id, "aria-selected": "false", tabindex: "-1",
        "data-person-tab": choice.id, class: "person-tab", text: M.t(choice.label), onclick: function (event) {
          if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
          event.preventDefault(); event.stopPropagation(); request(choice.id, true);
        } }));
    });
    var list = h("div", { role: "tablist", "aria-label": M.t("person.tabs"), class: "person-tabs" }, buttons);
    var node = h("div", { class: "person-tab-body" }, list, note,
      choices.map(function (choice) { return panels[choice.id]; }));

    function mode() {
      buttons.forEach(function (button, index) {
        button.hidden = M.mode() !== "detailed" && choices[index].id !== "access" && choices[index].id !== selected;
      });
      note.hidden = M.mode() === "detailed" || selected === "access";
    }
    function select(value) {
      var next = M.personTab(value), previous = selected;
      if (next !== previous) {
        changed(next, previous, panels[next], previous && panels[previous]);
        selected = next;
      }
      buttons.forEach(function (button, index) {
        var active = choices[index].id === selected;
        button.setAttribute("aria-selected", String(active)); button.tabIndex = active ? 0 : -1;
        panels[choices[index].id].hidden = !active;
      });
      mode();
    }
    function request(value, focus) {
      var next = M.personTab(value), hash = M.personHash(username, next);
      var work = selected === next ? Promise.resolve() : M.navigate ? M.navigate(hash) : M.tryLeave(node).then(function (leave) {
        if (!leave) return;
        select(next); location.hash = hash;
      });
      return Promise.resolve(work).then(function () {
        if (selected !== next || !node.isConnected) return false;
        if (focus) buttons.filter(function (button) { return button.dataset.personTab === next; })[0].focus();
        return true;
      });
    }
    list.addEventListener("keydown", function (event) {
      var visible = buttons.filter(function (button) { return !button.hidden; });
      var index = visible.indexOf(event.target), next = index, rtl = document.documentElement.dir === "rtl";
      if (index < 0) return;
      if (event.key === "ArrowRight") next += rtl ? -1 : 1;
      else if (event.key === "ArrowLeft") next += rtl ? 1 : -1;
      else if (event.key === "ArrowDown") next++;
      else if (event.key === "ArrowUp") next--;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = visible.length - 1;
      else return;
      event.preventDefault();
      request(visible[(next + visible.length) % visible.length].dataset.personTab, true);
    });
    return { node: node, select: select, request: request, mode: mode, initial: initial,
      selected: function () { return selected; }, panels: panels };
  };
})();
