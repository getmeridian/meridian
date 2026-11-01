/* People triage: one list read, URL-fragment state, and stable row nodes. */
(function () {
  "use strict";
  var M = window.M, h = M.h;
  var STATUSES = ["active", "disabled", "limited", "expired"];
  var REASONS = ["expiring", "quota", "unused", "stopped"];
  var SORTS = ["name", "usage", "expiry", "activity"];
  var FACETS = ["status", "tag", "expires", "quota", "never", "attention"];
  var DAY = 86400000;

  function integer(text, max) {
    return /^[0-9]{1,3}$/.test(text) && Number(text) >= 1 && Number(text) <= max ? String(Number(text)) : "";
  }
  function defaultOrder(sort) { return sort === "usage" || sort === "activity" ? "desc" : "asc"; }
  function readFilters() {
    var query = new URLSearchParams(M.route().query || "");
    function one(key) { var values = query.getAll(key); return values.length === 1 ? values[0] : ""; }
    var sort = SORTS.indexOf(one("sort")) >= 0 ? one("sort") : "name";
    var tag = one("tag");
    return {
      q: one("q").slice(0, 256), status: STATUSES.indexOf(one("status")) >= 0 ? one("status") : "",
      tag: tag === "none" || /^[A-Z0-9_]{1,16}$/.test(tag) ? tag : "",
      expires: integer(one("expires"), 365), quota: integer(one("quota"), 100), never: one("never") === "1",
      attention: one("attention") === "all" || REASONS.indexOf(one("attention")) >= 0 ? one("attention") : "",
      sort: sort, order: ["asc", "desc"].indexOf(one("order")) >= 0 ? one("order") : defaultOrder(sort),
    };
  }
  function persist(state) {
    var query = new URLSearchParams();
    ["q"].concat(FACETS).forEach(function (key) {
      if (state[key]) query.set(key, key === "never" ? "1" : state[key]);
    });
    if (state.sort !== "name") query.set("sort", state.sort);
    if (state.order !== defaultOrder(state.sort)) query.set("order", state.order);
    var hash = "#/people" + (query.toString() ? "?" + query.toString() : "");
    M.peopleHash = hash;
    if (M.replaceRouteHash) M.replaceRouteHash(hash);
    else history.replaceState(history.state, "", location.pathname + location.search + hash);
  }
  function timestamp(value) { var result = value ? Date.parse(value) : NaN; return isFinite(result) ? result : null; }
  function text(node, value) { if (node.textContent !== value) node.textContent = value; }
  function hidden(node, value) { if (node.hidden !== value) node.hidden = value; }

  M.renderPeople = function (view) {
    var state = readFilters(), entries = new Map(), ordered = [], dirtyOrder = true, searchTimer;
    var collator = new Intl.Collator(M.i18n.locale(), { numeric: true, sensitivity: "base" });
    var search = h("input", { type: "search", name: "q", class: "search", maxlength: "256",
      placeholder: M.t("people.search"), "aria-label": M.t("people.search"), autocomplete: "off" });
    var list = h("div", { class: "list live-data people-list" });
    var empty = h("div", { class: "empty" }, h("p", { text: M.t("people.empty") }));
    var loading = h("div", { class: "empty" }, h("span", { class: "spinner" }));
    var status = h("div", { class: "live-status" });
    var count = h("p", { class: "muted small", role: "status", "aria-live": "polite" });
    var controls = {}, counters = {}, errors = {};

    function select(name, keys, label) {
      var input = h("select", { name: name }, keys.map(function (key) {
        return h("option", { value: key, text: label(key) });
      }));
      controls[name] = input;
      input.value = state[name];
      return M.field(M.t("people.filter_" + name), input);
    }
    var statusField = select("status", [""].concat(STATUSES), function (key) {
      return key ? M.t("status." + key) : M.t("people.any_status");
    });
    var tagField = select("tag", ["", "none"], function (key) {
      return M.t(key ? "people.no_tag" : "people.any_tag");
    });
    var attentionField = select("attention", ["", "all"].concat(REASONS), function (key) {
      return key ? M.t(key === "all" ? "people.attention" : "attention.reason." + key) : M.t("people.any_attention");
    });
    function numeric(name, max) {
      var input = h("input", { name: name, type: "number", min: "1", max: String(max), step: "1",
        placeholder: M.t("people.filter_off"), inputmode: "numeric" });
      controls[name] = input;
      input.value = state[name];
      var box = M.field(M.t("people.filter_" + name), input);
      counters[name] = h("p", { class: "hint" });
      errors[name] = h("p", { class: "field-error", role: "alert" });
      box.appendChild(counters[name]); box.appendChild(errors[name]);
      input.addEventListener("input", function () {
        var value = input.value.trim(), normalized = integer(value, max);
        var bad = input.validity.badInput || !!value && !normalized;
        var error = bad ? M.t(name === "expires" ? "people.expires_invalid" : "people.quota_invalid") : "";
        text(errors[name], error);
        input.setAttribute("aria-invalid", bad ? "true" : "false");
        if (bad) return;
        state[name] = normalized;
        changed(false);
      });
      return box;
    }
    var expiresField = numeric("expires", 365), quotaField = numeric("quota", 100);
    controls.never = h("input", { type: "checkbox", name: "never" });
    controls.never.checked = state.never;
    counters.never = h("span", { class: "hint" });
    var neverField = h("div", { class: "field people-never" },
      h("label", { class: "check-row" }, controls.never, h("span", { text: M.t("people.filter_never") })), counters.never);
    var sortField = select("sort", SORTS, function (key) { return M.t("people.sort_" + key); });
    var orderField = select("order", ["asc", "desc"], function (key) { return M.t("people.order_" + key); });
    var clear = h("button", { type: "button", class: "btn btn-ghost btn-sm", text: M.t("people.clear_filters"),
      onclick: function () {
        state = { q: "", status: "", tag: "", expires: "", quota: "", never: false, attention: "", sort: "name", order: "asc" };
        search.value = "";
        Object.keys(controls).forEach(function (key) {
          if (key === "never") controls[key].checked = false;
          else controls[key].value = state[key];
          controls[key].removeAttribute("aria-invalid");
        });
        Object.keys(errors).forEach(function (key) { text(errors[key], ""); });
        dirtyOrder = true; changed(false); search.focus();
      } });

    function changed(debounce) {
      persist(state);
      window.clearTimeout(searchTimer);
      if (debounce) searchTimer = window.setTimeout(draw, 100);
      else draw();
    }
    search.value = state.q;
    search.addEventListener("input", function () { state.q = search.value.slice(0, 256); changed(true); });
    ["status", "tag", "attention", "never", "sort", "order"].forEach(function (key) {
      controls[key].addEventListener("change", function () {
        state[key] = key === "never" ? controls[key].checked : controls[key].value;
        if (key === "sort") { state.order = defaultOrder(state.sort); controls.order.value = state.order; }
        if (key === "sort" || key === "order") dirtyOrder = true;
        changed(false);
      });
    });
    var filters = h("details", { class: "card people-filter-card", open: FACETS.some(function (key) { return !!state[key]; }) },
      h("summary", { text: M.t("people.filters") }),
      h("div", { class: "people-filters" }, statusField, tagField, expiresField, quotaField, neverField, attentionField),
      h("div", { class: "people-sort" }, sortField, orderField, clear));
    M.mount(view, [
      h("div", { class: "page-head" },
        h("div", {}, h("h1", { text: M.t("nav.people") }), h("p", { class: "muted", text: M.t("people.lede") })),
        h("button", { class: "btn btn-primary", type: "button", onclick: M.addPerson }, M.icon("plus"), M.t("people.add"))),
      search,
      filters,
      count, status, list,
    ]);
    M.mount(list, [loading, empty]); empty.hidden = true;
    persist(state);

    function update(entry, user) {
      var old = entry.user;
      entry.name = M.displayName(user.username);
      entry.search = (user.username + "\n" + entry.name).toLowerCase();
      entry.expiry = timestamp(user.expire_at); entry.activity = timestamp(user.online_at);
      entry.reasons = (user.attention_reasons || []).filter(function (key) { return REASONS.indexOf(key) >= 0; });
      text(entry.label, entry.name);
      text(entry.expiryText, M.expiryText(user));
      text(entry.activityText, M.t("people.online", { when: M.ago(user.online_at) }));
      var declared = M.isDeclared(user.username);
      if (!old || old.tag !== user.tag || entry.declared !== declared) {
        M.mount(entry.tags, [declared ? h("span", { class: "tag", title: M.t("people.declared_hint"), text: M.t("people.declared") }) : null,
          user.tag ? h("bdi", { class: "person-tag", text: user.tag }) : null]);
      }
      if (!old || old.status !== user.status) M.mount(entry.status, M.statusPill(user.status));
      if (!old || old.used_traffic_bytes !== user.used_traffic_bytes || old.traffic_limit_bytes !== user.traffic_limit_bytes) {
        M.mount(entry.usage, M.usage(user));
      }
      var reasons = entry.reasons.join(",");
      if (reasons !== entry.reasonText) M.mount(entry.reasonSlot, entry.reasons.map(function (key) {
        return h("span", { class: "person-reason", text: M.t("attention.reason." + key) });
      }));
      entry.reasonText = reasons; entry.declared = declared; entry.user = user;
    }
    function makeEntry(user) {
      var entry = {
        label: h("span", { class: "name" }), tags: h("span", { class: "people-tags" }),
        expiryText: h("span"), activityText: h("span"), reasonSlot: h("div", { class: "people-reasons" }),
        status: h("div"), usage: h("div"),
      };
      entry.row = h("a", { class: "row row-link", href: "#/people/" + encodeURIComponent(user.username) },
        h("div", { class: "row-main" }, h("div", { class: "row-title" }, entry.label, entry.tags),
          h("div", { class: "row-sub" }, entry.expiryText, entry.activityText), entry.reasonSlot),
        h("div", { class: "row-side" }, entry.status, entry.usage));
      update(entry, user);
      return entry;
    }
    function compare(a, b) {
      var difference = 0, key = state.sort;
      if (key === "name") difference = collator.compare(a.name, b.name);
      else if (key === "usage") difference = (a.user.used_traffic_bytes || 0) - (b.user.used_traffic_bytes || 0);
      else {
        var left = key === "expiry" ? a.expiry : a.activity, right = key === "expiry" ? b.expiry : b.activity;
        if (left === null && right !== null) return 1;
        if (right === null && left !== null) return -1;
        difference = left === null ? 0 : left - right;
      }
      if (difference) return state.order === "desc" ? -difference : difference;
      return collator.compare(a.name, b.name) || collator.compare(a.user.username, b.user.username);
    }
    function setOptions(input, values, counts, label) {
      values.forEach(function (key) {
        var option = Array.from(input.options).find(function (item) { return item.value === key; });
        if (!option) { option = h("option", { value: key }); input.appendChild(option); }
        text(option, M.t("people.option_count", { name: label(key), count: M.count(counts.get(key) || 0) }));
      });
      Array.from(input.options).forEach(function (option) { if (values.indexOf(option.value) < 0) option.remove(); });
      input.value = state[input.name];
    }
    function draw() {
      if (dirtyOrder) {
        ordered = Array.from(entries.values()).sort(compare);
        var focus = document.activeElement;
        var fragment = document.createDocumentFragment();
        ordered.forEach(function (entry) { fragment.appendChild(entry.row); });
        list.insertBefore(fragment, empty);
        if (focus && list.contains(focus)) focus.focus({ preventScroll: true });
        dirtyOrder = false;
      }
      var counts = { status: new Map(), tag: new Map(), attention: new Map(), expires: 0, quota: 0, never: 0 };
      var q = state.q.trim().toLowerCase(), now = Date.now(), shown = 0;
      function add(map, key) { map.set(key, (map.get(key) || 0) + 1); }
      ordered.forEach(function (entry) {
        var user = entry.user, tag = user.tag || "none";
        if (q && entry.search.indexOf(q) < 0) { hidden(entry.row, true); return; }
        var passes = {
          status: !state.status || user.status === state.status,
          tag: !state.tag || tag === state.tag,
          expires: !state.expires || entry.expiry !== null && entry.expiry >= now && entry.expiry <= now + Number(state.expires) * DAY,
          quota: !state.quota || user.quota_used_percent !== null && user.quota_used_percent !== undefined && user.quota_used_percent >= Number(state.quota),
          never: !state.never || user.never_used === true,
          attention: !state.attention || (state.attention === "all" ? entry.reasons.length > 0 : entry.reasons.indexOf(state.attention) >= 0),
        };
        var failures = FACETS.filter(function (key) { return !passes[key]; });
        hidden(entry.row, failures.length !== 0);
        if (!failures.length) shown++;
        FACETS.forEach(function (key) {
          if (failures.length && !(failures.length === 1 && failures[0] === key)) return;
          if (key === "status") { add(counts.status, ""); add(counts.status, user.status); }
          else if (key === "tag") { add(counts.tag, ""); add(counts.tag, tag); }
          else if (key === "attention") {
            add(counts.attention, "");
            if (entry.reasons.length) add(counts.attention, "all");
            entry.reasons.forEach(function (reason) { add(counts.attention, reason); });
          } else if (key === "never") { if (user.never_used === true) counts.never++; }
          else if (passes[key]) counts[key]++;
        });
      });
      var tags = Array.from(new Set(ordered.map(function (entry) { return entry.user.tag || "none"; })));
      if (state.tag && tags.indexOf(state.tag) < 0) tags.push(state.tag);
      tags = ["", "none"].concat(tags.filter(function (key) { return key !== "none"; }).sort());
      setOptions(controls.status, [""].concat(STATUSES), counts.status, function (key) { return key ? M.t("status." + key) : M.t("people.any_status"); });
      setOptions(controls.tag, tags, counts.tag, function (key) { return key === "" ? M.t("people.any_tag") : key === "none" ? M.t("people.no_tag") : key; });
      setOptions(controls.attention, ["", "all"].concat(REASONS), counts.attention, function (key) {
        return key ? M.t(key === "all" ? "people.attention" : "attention.reason." + key) : M.t("people.any_attention");
      });
      ["expires", "quota", "never"].forEach(function (key) { text(counters[key], M.t("people.filter_count", { count: M.count(counts[key]) })); });
      text(count, M.t("people.shown_count", { shown: M.count(shown), total: M.count(ordered.length) }));
      text(empty.querySelector("p"), M.t(ordered.length ? "people.no_filter_match" : "people.empty"));
      empty.hidden = shown > 0;
    }
    var stop = M.liveView(view, {
      status: status, interval: 30000,
      load: function (passive) {
        return Promise.all([M.api("GET", "api/users", undefined, { passive: passive }),
          M.api("GET", "ui/api/fleet", undefined, { passive: passive }).catch(function () { return M.state.fleet || {}; })]);
      },
      draw: function (results) {
        M.state.fleet = results[1];
        var seen = new Set();
        (results[0].users || []).forEach(function (user) {
          seen.add(user.username);
          var entry = entries.get(user.username);
          if (entry) update(entry, user);
          else entries.set(user.username, makeEntry(user));
        });
        entries.forEach(function (entry, name) { if (!seen.has(name)) { entry.row.remove(); entries.delete(name); } });
        loading.remove(); dirtyOrder = true; draw();
      },
      empty: function () { loading.remove(); },
    });
    return function () { window.clearTimeout(searchTimer); stop(); };
  };
})();
