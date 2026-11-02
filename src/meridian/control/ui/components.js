/* Small shared components; every value stays a text node or a native control. */
(function () {
  "use strict";
  var M = window.M, h = M.h;

  M.segmented = function (label, choices, selected, choose, className, attribute) {
    var buttons = choices.map(function (choice) {
      var attrs = { type: "button", role: "radio", text: choice.label,
        onclick: function () { choose(choice.value); } };
      attrs[attribute] = choice.value;
      return h("button", attrs);
    });
    var group = h("div", { class: "segmented " + (className || ""), role: "radiogroup", "aria-label": label }, buttons);
    function update(value) {
      buttons.forEach(function (button, index) {
        var active = choices[index].value === value;
        button.classList.toggle("active", active);
        button.setAttribute("aria-checked", String(active));
        button.tabIndex = active ? 0 : -1;
      });
    }
    group.addEventListener("keydown", function (event) {
      var index = buttons.indexOf(event.target), next = index;
      if (index < 0) return;
      var rtl = document.documentElement.dir === "rtl";
      if (event.key === "ArrowRight") next += rtl ? -1 : 1;
      else if (event.key === "ArrowLeft") next += rtl ? 1 : -1;
      else if (event.key === "ArrowDown") next++;
      else if (event.key === "ArrowUp") next--;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = buttons.length - 1;
      else return;
      event.preventDefault();
      next = (next + buttons.length) % buttons.length;
      buttons[next].click(); buttons[next].focus();
    });
    update(selected);
    return { node: group, update: update };
  };

  M.statusChip = function (kind, text) {
    return M.pill({ success: "ok", warn: "warn", danger: "err", neutral: "muted" }[kind] || "muted", text);
  };

  M.meter = function (percent, label, kind) {
    return h("meter", { class: "meter meter-" + (kind || "success"), min: "0", max: "100",
      value: Math.max(0, Math.min(100, percent)), "aria-label": label }, label);
  };

  M.keyValue = function (label, value) {
    return h("div", { class: "kv-row" }, h("dt", { text: label }), h("dd", {}, value));
  };

  M.emptyState = function (title, caption, check) {
    return h("div", { class: "empty-state", role: "status" },
      check ? h("span", { class: "empty-state-check", "aria-hidden": "true" }, M.icon("check")) : null,
      h("p", { class: "strong", text: title }), caption ? h("p", { class: "muted small", text: caption }) : null);
  };

  M.detailedSection = function (label, content, key) {
    return h("details", { class: "mode-disclosure", "data-mode-section": key, open: M.mode() === "detailed" },
      h("summary", {}, h("span", { text: label }),
        h("span", { class: "mode-caption muted small", hidden: M.mode() === "detailed", text: M.t("mode.shown_detailed") })),
      h("div", { class: "mode-content" }, content));
  };
})();
