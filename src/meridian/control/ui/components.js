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

  /* A fact row; an optional tip sits beside the label, outside the label text other controls refer to. */
  M.keyValue = function (label, value, tip) {
    var term = tip ? h("dt", { class: "has-tip" }, h("span", { class: "term", text: label }), M.tip(label, tip)) : h("dt", { text: label });
    return h("div", { class: "kv-row" }, term, h("dd", {}, value));
  };

  /* A heading with its explanation beside it, so the heading's own name stays just its text. */
  M.titled = function (level, text, tip) {
    return h("div", { class: "title-row" }, h(level, { text: text }), M.tip(text, tip));
  };

  M.arrowPair = function (first, second, quiet) {
    var direction = document.documentElement.dir || "ltr";
    return h("span", { class: "arrow-pair", dir: direction }, h("bdi", {}, first),
      h("span", { "aria-hidden": quiet ? "true" : null, text: direction === "rtl" ? "←" : "→" }),
      h("bdi", {}, second));
  };

  M.emptyState = function (title, caption, check) {
    return h("div", { class: "empty-state", role: "status" },
      check ? h("span", { class: "empty-state-check", "aria-hidden": "true" }, M.icon("check")) : null,
      h("p", { class: "strong", text: title }), caption ? h("p", { class: "muted small", text: caption }) : null);
  };

  M.learnMore = function (content) {
    return h("details", { class: "learn-more" }, h("summary", { text: M.t("action.learn_more") }),
      h("div", { class: "stack-sm muted small" }, content));
  };

  M.rowMenu = function (name, actions) {
    var summary = h("summary", { class: "btn btn-ghost btn-sm", "aria-label": M.t("records.actions_for", { name: name }) },
      M.t("action.more"));
    var menu = h("details", { class: "row-menu" }, summary, h("div", { class: "row-menu-panel card" }, actions));
    menu.addEventListener("click", function (event) {
      if (menu.querySelector(".row-menu-panel").contains(event.target) && event.target.closest && event.target.closest("button, a")) {
        menu.open = false; summary.focus({ preventScroll: true });
      }
    }, true);
    menu.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && menu.open) {
        event.preventDefault(); event.stopPropagation(); menu.open = false; summary.focus();
      }
    });
    return menu;
  };
  document.addEventListener("click", function (event) {
    document.querySelectorAll(".row-menu[open]").forEach(function (menu) {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });

  /* A toggletip: an info button that shows a plain explanation on click, tap, or Enter. The bubble is a
   * live region filled only while open, so screen readers announce it; a second press, Escape, or a click
   * elsewhere closes it. It is fixed to the viewport so cards and scrolling panels never clip it. */
  var tipSequence = 0;
  function closeTips(except) {
    document.querySelectorAll(".tip").forEach(function (tip) { if (tip !== except && tip.meridianClose) tip.meridianClose(); });
  }
  M.tip = function (topic, content) {
    var id = "tip-" + (++tipSequence);
    // tabindex -1 lets a click inside the bubble keep focus within the tip, so Tab and Escape still close it.
    var bubble = h("span", { class: "tip-bubble", id: id, role: "status", tabindex: "-1", hidden: true });
    var button = h("button", { class: "tip-button", type: "button", "aria-label": M.t("tip.about", { topic: topic }),
      "aria-expanded": "false", "aria-controls": id }, M.icon("info"));
    var tip = h("span", { class: "tip" }, button, bubble);
    function gone() {
      if (!button.isConnected || button.closest("[hidden]")) return true;
      var closed = button.closest("details:not([open])"), summary = closed && closed.querySelector("summary");
      return !!closed && !(summary && summary.contains(button));
    }
    function place() {
      // A tip whose button left the page or a closed disclosure closes, so its listeners never linger.
      if (gone()) { show(false); return; }
      // Measure from the corner, or the static position squeezes the bubble's width near an edge.
      bubble.style.left = "0px"; bubble.style.top = "0px";
      var margin = 8, rect = button.getBoundingClientRect(), width = bubble.offsetWidth, height = bubble.offsetHeight;
      var room = document.documentElement.clientWidth, below = rect.bottom + 6, above = rect.top - 6 - height;
      bubble.style.left = Math.max(margin, Math.min(rect.left + rect.width / 2 - width / 2, room - width - margin)) + "px";
      bubble.style.top = (below + height > window.innerHeight - margin && above >= margin ? above : below) + "px";
    }
    function show(open) {
      if (open) closeTips(tip);
      button.setAttribute("aria-expanded", String(open));
      bubble.hidden = !open;
      M.mount(bubble, open ? content : null);
      if (open) {
        window.addEventListener("scroll", place, true);
        window.addEventListener("resize", tip.meridianClose);
        place();
      } else {
        window.removeEventListener("scroll", place, true);
        window.removeEventListener("resize", tip.meridianClose);
      }
    }
    tip.meridianClose = function () { if (!bubble.hidden) show(false); };
    // preventDefault keeps a tip inside a label or summary from toggling its control.
    button.addEventListener("click", function (event) { event.preventDefault(); show(bubble.hidden); });
    tip.addEventListener("focusout", function (event) {
      if (!bubble.hidden && event.relatedTarget && !tip.contains(event.relatedTarget)) show(false);
    });
    tip.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || bubble.hidden) return;
      event.preventDefault(); event.stopPropagation(); show(false); button.focus();
    });
    return tip;
  };
  document.addEventListener("click", function (event) {
    if (!event.target.closest || !event.target.closest(".tip")) closeTips();
  });
  // A closing disclosure takes its open tips with it; toggle events don't bubble, so listen in capture.
  document.addEventListener("toggle", function (event) {
    var details = event.target, summary = details.querySelector && details.querySelector("summary");
    if (details.open === false && details.querySelectorAll) {
      details.querySelectorAll(".tip").forEach(function (tip) {
        if (tip.meridianClose && !(summary && summary.contains(tip))) tip.meridianClose();
      });
    }
  }, true);
  // A mode change can move rows into closed disclosures without a toggle event.
  window.addEventListener("meridian:mode", function () { closeTips(); });

  M.detailedSection = function (label, content, key) {
    return h("details", { class: "mode-disclosure", "data-mode-section": key, open: M.mode() === "detailed" },
      h("summary", { text: label }),
      h("div", { class: "mode-content" }, content));
  };
})();
