/* One server query, fragment state, bounded pages, and stable person rows. */
(function () {
  "use strict";
  var M = window.M, h = M.h;
  var STATUSES = ["active", "disabled", "limited", "expired"];
  var REASONS = ["expiring", "quota", "unused", "stopped"];
  var SORTS = ["name", "usage", "expiry", "activity"];
  var LIFECYCLES = ["pending", "renewing", "fixed", "unlimited"];
  var FACETS = ["status", "tag", "profile", "expires", "quota", "never", "attention", "lifecycle"];
  var FILTER_ORDER = ["status", "expires", "quota", "tag", "profile", "never", "attention", "lifecycle", "sort"];
  var STATUS_KINDS = { active: "success", disabled: "neutral", limited: "warn", expired: "danger" };

  function integer(text, max) {
    return /^[0-9]{1,3}$/.test(text) && Number(text) >= 1 && Number(text) <= max ? String(Number(text)) : "";
  }
  function defaultOrder(sort) { return sort === "usage" || sort === "activity" ? "desc" : "asc"; }
  function defaults() { return { q: "", status: "", tag: "", profile: "", expires: "", quota: "", never: false, attention: "", lifecycle: "", sort: "name", order: "asc" }; }
  function readFilters() {
    var query = new URLSearchParams(M.route().query || "");
    function one(key) { var values = query.getAll(key); return values.length === 1 ? values[0] : ""; }
    var state = defaults(), sort = SORTS.indexOf(one("sort")) >= 0 ? one("sort") : "name";
    state.q = one("q").slice(0, 256);
    state.status = STATUSES.indexOf(one("status")) >= 0 ? one("status") : "";
    state.tag = one("tag") === "none" || /^[A-Z0-9_]{1,16}$/.test(one("tag")) ? one("tag") : "";
    state.profile = /^[a-z0-9][a-z0-9_-]{0,47}$/.test(one("profile")) ? one("profile") : "";
    state.expires = integer(one("expires"), 365); state.quota = integer(one("quota"), 100);
    state.never = one("never") === "1";
    state.attention = one("attention") === "all" || REASONS.indexOf(one("attention")) >= 0 ? one("attention") : "";
    state.lifecycle = LIFECYCLES.indexOf(one("lifecycle")) >= 0 ? one("lifecycle") : "";
    state.sort = sort;
    state.order = ["asc", "desc"].indexOf(one("order")) >= 0 ? one("order") : defaultOrder(sort);
    return state;
  }
  function persist(state) {
    var query = new URLSearchParams();
    ["q"].concat(FACETS).forEach(function (key) { if (state[key]) query.set(key, key === "never" ? "1" : state[key]); });
    if (state.sort !== "name") query.set("sort", state.sort);
    if (state.order !== defaultOrder(state.sort)) query.set("order", state.order);
    var hash = "#/people" + (query.toString() ? "?" + query.toString() : "");
    M.peopleHash = hash;
    if (M.replaceRouteHash) M.replaceRouteHash(hash);
    else history.replaceState(history.state, "", location.pathname + location.search + hash);
  }
  function text(node, value) { if (node.textContent !== value) node.textContent = value; }
  function number(value) { return "\u2068" + M.count(value) + "\u2069"; }
  function usageText(user) {
    var used = user.used_traffic_bytes, limit = user.traffic_limit_bytes;
    if (!limit) return M.t("people.usage_unlimited", { used: M.bytes(used) });
    var units = ["b", "kib", "mib", "gib", "tib", "pib"], index = 0, divisor = 1;
    while (limit / divisor >= 1024 && index < units.length - 1) { divisor *= 1024; index++; }
    if (used && used / divisor < 0.1) return M.t("people.used_of", { used: M.bytes(used), limit: M.bytes(limit) });
    function amount(value) {
      return new Intl.NumberFormat(M.i18n.locale(), { maximumFractionDigits: index && value / divisor < 10 ? 1 : 0 }).format(value / divisor);
    }
    return M.t("people.used_of", { used: "\u2068" + amount(used) + "\u2069",
      limit: "\u2068" + amount(limit) + " " + M.t("unit." + units[index]) + "\u2069" });
  }

  M.renderPeople = function (view) {
    var state = readFilters(), entries = new Map(), ordered = [], selected = new Set(), matching = false;
    var cursor = null, previous = [], page = null, searchTimer, stop, lastSelected = null, ready = false;
    var pageCursor = null, pagePrevious = [], pageQuery = null;
    var controls = {}, counters = {}, errors = {}, filterMenus = {}, filterSummaries = {}, selecting = false;
    var search = h("input", { type: "search", name: "q", class: "search", maxlength: "256", placeholder: M.t("people.search"),
      "aria-label": M.t("people.search"), autocomplete: "off" });
    var list = h("div", { class: "list live-data people-list" });
    var empty = M.emptyState(""), emptyTitle = empty.querySelector("p"), emptyCaption = h("p", { class: "muted small" });
    var emptyAdd = h("button", { class: "btn btn-primary", type: "button", "data-empty-add": "", hidden: true, onclick: M.addPerson }, M.icon("plus"), M.t("people.add"));
    var emptyClear = h("button", { class: "btn btn-ghost", type: "button", "data-empty-clear": "", hidden: true, text: M.t("people.clear_filters"), onclick: clearFilters });
    empty.appendChild(emptyCaption); empty.appendChild(h("div", { class: "row-actions" }, emptyAdd, emptyClear));
    var activeChips = h("div", { class: "people-active-filters", hidden: true, role: "group", "aria-label": M.t("people.active_filters") });
    var loading = h("div", { class: "empty" }, h("span", { class: "spinner" }));
    var status = h("div", { class: "live-status" }), count = h("p", { class: "muted small", role: "status", "aria-live": "polite" });
    function query() {
      return { q: state.q, status: state.status, tag: state.tag, profile: state.profile, expires: state.expires ? Number(state.expires) : null,
        quota: state.quota ? Number(state.quota) : null, never: state.never, attention: state.attention,
        lifecycle: state.lifecycle, sort: state.sort, order: state.order, limit: 100 };
    }
    function clearSelection() {
      selected.clear(); matching = false; lastSelected = null;
      ordered.forEach(function (entry) { entry.select.checked = false; entry.select.disabled = false; });
      selectionChanged();
    }
    var bulk = M.peopleBulkBar({
      selection: function () { return matching ? { query: query() } : { usernames: Array.from(selected) }; },
      clear: function () { clearSelection(); (M.mode() === "detailed" ? pageSelect : selectToggle).focus(); },
      done: function () { clearSelection(); if (view.isConnected && stop) stop.refresh(false); },
    });
    function selectionChanged() {
      var total = page ? page.total : 0;
      pageSelect.checked = !!ordered.length && ordered.every(function (entry) { return matching || selected.has(entry.user.username); });
      pageSelect.indeterminate = !pageSelect.checked && ordered.some(function (entry) { return selected.has(entry.user.username); });
      pageSelect.disabled = !ready || matching;
      bulk.update(matching ? total : selected.size, total, matching, ready, pageSelect.checked);
      pageRoot.classList.toggle("has-selection", (matching ? total : selected.size) > 0);
      pageRoot.classList.toggle("has-matching-offer", !bulk.matching.hidden && !bulk.nodes.hidden);
    }
    var pageSelect = h("input", { type: "checkbox", name: "select-page", "aria-label": M.t("bulk.select_page") });
    var pageSelection = h("label", { class: "check-row select-page", hidden: M.mode() !== "detailed" }, pageSelect, h("span", { text: M.t("bulk.select_page") }));
    var selectToggle = h("button", { class: "btn btn-ghost btn-sm", type: "button", "data-people-select": "", hidden: M.mode() === "detailed",
      "aria-pressed": "false", text: M.t("people.select"), onclick: function () {
        selecting = !selecting;
        if (!selecting) clearSelection();
        applySelectionMode();
      } });
    function applySelectionMode() {
      var detailed = M.mode() === "detailed", show = detailed || selecting;
      selectToggle.hidden = detailed;
      selectToggle.setAttribute("aria-pressed", String(selecting));
      text(selectToggle, M.t(selecting ? "people.select_done" : "people.select"));
      pageSelection.hidden = !show;
      entries.forEach(function (entry) { entry.selection.hidden = !show; });
    }
    pageSelect.addEventListener("change", function () {
      ordered.forEach(function (entry) {
        if (pageSelect.checked) selected.add(entry.user.username); else selected.delete(entry.user.username);
        entry.select.checked = pageSelect.checked;
      });
      selectionChanged();
    });
    bulk.matching.addEventListener("click", function () {
      if (!ready || !pageSelect.checked || matching) return;
      matching = true; selected.clear();
      ordered.forEach(function (entry) { entry.select.checked = true; entry.select.disabled = true; });
      selectionChanged();
    });
    function changed(debounce) {
      persist(state); drawChips(); cursor = null; previous = []; ready = false; clearSelection();
      clearTimeout(searchTimer);
      if (debounce) searchTimer = setTimeout(function () { stop.refresh(false); }, 150);
      else if (stop) stop.refresh(false);
    }
    function select(name, keys, label) {
      var input = h("select", { name: name }, keys.map(function (key) { return h("option", { value: key, text: label(key) }); }));
      controls[name] = input; input.value = state[name];
      input.addEventListener("change", function () {
        state[name] = input.value;
        if (name === "sort") { state.order = defaultOrder(state.sort); controls.order.value = state.order; }
        changed(false);
      });
      return M.field(M.t("people.filter_" + name), input);
    }
    var statusField = select("status", [""].concat(STATUSES), function (key) { return key ? M.t("status." + key) : M.t("people.any_status"); });
    var tagField = select("tag", ["", "none"].concat(state.tag && state.tag !== "none" ? [state.tag] : []), function (key) { return key === "" ? M.t("people.any_tag") : key === "none" ? M.t("people.no_tag") : key; });
    function profileLabel(key) { return key === "" ? M.t("people.any_profile") : key === "default" ? M.t("people.default_profile") : key; }
    var profileField = select("profile", ["", "default"].concat(state.profile && state.profile !== "default" ? [state.profile] : []), profileLabel);
    var attentionField = select("attention", ["", "all"].concat(REASONS), function (key) { return key ? M.t(key === "all" ? "people.attention" : "attention.reason." + key) : M.t("people.any_attention"); });
    var lifecycleField = select("lifecycle", [""].concat(LIFECYCLES), function (key) { return M.t("people.lifecycle_" + (key || "any")); });
    function numeric(name, max) {
      var input = h("input", { name: name, type: "number", min: "1", max: String(max), step: "1", inputmode: "numeric", placeholder: M.t("people.filter_off") });
      controls[name] = input; input.value = state[name];
      counters[name] = h("p", { class: "hint" }); errors[name] = h("p", { class: "field-error", role: "alert" });
      var box = M.field(M.t("people.filter_" + name), input); box.appendChild(counters[name]); box.appendChild(errors[name]);
      input.addEventListener("input", function () {
        var value = input.value.trim(), normalized = integer(value, max), bad = input.validity.badInput || !!value && !normalized;
        text(errors[name], bad ? M.t(name === "expires" ? "people.expires_invalid" : "people.quota_invalid") : "");
        input.setAttribute("aria-invalid", bad ? "true" : "false");
        if (bad) return;
        state[name] = normalized; changed(false);
      });
      return box;
    }
    var expiresField = numeric("expires", 365), quotaField = numeric("quota", 100);
    controls.never = h("input", { type: "checkbox", name: "never" }); controls.never.checked = state.never;
    counters.never = h("span", { class: "hint" });
    controls.never.addEventListener("change", function () { state.never = controls.never.checked; changed(false); });
    var neverField = h("div", { class: "field people-never" }, h("label", { class: "check-row" }, controls.never, h("span", { text: M.t("people.filter_never") })), counters.never);
    var sortField = select("sort", SORTS, function (key) { return M.t("people.sort_" + key); });
    var orderField = select("order", ["asc", "desc"], function (key) { return M.t("people.order_" + key); });
    function clearFilters() {
      state = defaults(); search.value = "";
      Object.keys(controls).forEach(function (key) { if (key === "never") controls[key].checked = false; else controls[key].value = state[key]; controls[key].removeAttribute("aria-invalid"); });
      Object.keys(errors).forEach(function (key) { text(errors[key], ""); });
      closeMenus();
      changed(false); search.focus();
    }
    function chipLabel(name) {
      if (name === "q") return M.t("people.search");
      if (["expires", "quota", "profile", "lifecycle", "sort"].indexOf(name) >= 0) return M.t("people.chip_" + name);
      return M.t("people.filter_" + name);
    }
    function chipValue(name) {
      if (name === "status") return M.t("status." + state.status);
      if (name === "tag") return state.tag === "none" ? M.t("people.no_tag") : state.tag;
      if (name === "profile") return profileLabel(state.profile);
      if (name === "expires") return M.t("people.chip_expires_value", { days: number(state.expires) });
      if (name === "quota") return M.t("people.chip_quota_value", { percent: M.percent(Number(state.quota)) });
      if (name === "never") return "";
      if (name === "attention") return M.t(state.attention === "all" ? "people.attention" : "attention.reason." + state.attention);
      if (name === "lifecycle") return M.t("people.lifecycle_" + state.lifecycle);
      if (name === "sort") return M.t("people.sort_" + state.sort) + " · " + M.t("people.order_" + state.order);
      return state.q;
    }
    function removeFilter(name) {
      if (name === "sort") { state.sort = "name"; state.order = "asc"; controls.sort.value = state.sort; controls.order.value = state.order; }
      else if (name === "q") { state.q = ""; search.value = ""; }
      else {
        state[name] = name === "never" ? false : "";
        if (name === "never") controls[name].checked = false; else controls[name].value = "";
        controls[name].removeAttribute("aria-invalid");
        if (errors[name]) text(errors[name], "");
      }
      changed(false);
      (name === "q" ? search : filterSummaries[name]).focus();
    }
    function drawChips() {
      var names = ["q"].concat(FILTER_ORDER).filter(function (name) {
        return name === "sort" ? state.sort !== "name" || state.order !== "asc" : !!state[name];
      });
      activeChips.hidden = !names.length;
      M.mount(activeChips, names.map(function (name) {
        var value = chipValue(name), label = chipLabel(name) + (value ? ": " + value : "");
        return h("button", { class: "people-active-chip", type: "button", "data-remove-filter": name,
          "aria-label": M.t("people.remove_filter", { filter: label }), onclick: function () { removeFilter(name); } },
          h("bdi", { text: label }), M.icon("x"));
      }).concat(names.length ? [h("button", { class: "btn btn-ghost btn-sm", type: "button", text: M.t("people.clear_filters"), onclick: clearFilters })] : []));
      FILTER_ORDER.forEach(function (name) { filterMenus[name].classList.toggle("is-active", names.indexOf(name) >= 0); });
    }
    function closeMenus(except) {
      Object.keys(filterMenus).forEach(function (name) { if (name !== except) filterMenus[name].open = false; });
      bulk.close();
    }
    function filterChip(name, content) {
      var summary = h("summary", {}, h("span", { text: chipLabel(name) }), h("span", { "aria-hidden": "true", text: "▾" }));
      var menu = h("details", { class: "people-filter", "data-filter": name }, summary, h("div", { class: "card people-filter-panel stack" }, content));
      filterMenus[name] = menu; filterSummaries[name] = summary;
      summary.addEventListener("click", function () { closeMenus(name); });
      menu.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && menu.open) { event.preventDefault(); menu.open = false; summary.focus(); }
      });
      return menu;
    }
    search.value = state.q;
    search.addEventListener("input", function () { state.q = search.value.slice(0, 256); changed(true); });
    var fields = { status: statusField, expires: expiresField, quota: quotaField, tag: tagField, profile: profileField,
      never: neverField, attention: attentionField, lifecycle: lifecycleField, sort: [sortField, orderField] };
    var filters = h("div", { class: "people-filter-chips", "aria-label": M.t("people.filters") }, FILTER_ORDER.map(function (name) { return filterChip(name, fields[name]); }));
    var previousButton = h("button", { class: "btn btn-ghost btn-sm", type: "button", disabled: true, text: M.t("people.previous"), onclick: function () { cursor = previous.pop(); stop.refresh(false); } });
    var nextButton = h("button", { class: "btn btn-ghost btn-sm", type: "button", disabled: true, text: M.t("people.next"), onclick: function () { previous.push(cursor); cursor = page.next_cursor; stop.refresh(false); } });
    var pageRoot = h("div", { class: "people-page" },
      h("div", { class: "page-head" }, h("div", {}, h("h1", { text: M.t("nav.people") }), h("p", { class: "muted", text: M.t("people.lede") })),
        h("button", { class: "btn btn-primary", type: "button", onclick: M.addPerson }, M.icon("plus"), M.t("people.add"))),
      h("div", { class: "people-toolbar", role: "search", "aria-label": M.t("people.search") }, search, filters), activeChips,
      h("div", { class: "people-list-header" }, h("div", { class: "people-list-meta" }, count, status), h("div", { class: "row-actions" }, selectToggle, pageSelection, bulk.receipt)),
      bulk.nodes, list,
      h("div", { class: "actions people-pager" }, previousButton, nextButton),
    );
    M.mount(view, pageRoot);
    M.mount(list, [loading, empty]); empty.hidden = true; persist(state); drawChips();
    function update(entry, user) {
      var old = entry.user, name = user.display_name || user.username;
      text(entry.label, name); text(entry.expiry, M.expiryText(user));
      entry.expiry.title = user.expire_at ? M.date(user.expire_at, true) + " · \u2068" + user.expire_at + "\u2069" : "";
      text(entry.activity, M.t("people.online", { when: M.ago(user.online_at) }));
      entry.select.setAttribute("aria-label", M.t("bulk.select_person", { name: name }));
      entry.select.checked = matching || selected.has(user.username); entry.select.disabled = matching;
      entry.selection.hidden = M.mode() !== "detailed" && !selecting;
      entry.declared.hidden = !user.declared; entry.notes.hidden = !user.has_notes;
      if (!old || old.tag !== user.tag) M.mount(entry.tags, user.tag ? h("bdi", { class: "person-tag", text: user.tag }) : null);
      if (!old || old.status !== user.status) M.mount(entry.status, M.statusChip(STATUS_KINDS[user.status], M.t("status." + user.status)));
      if (!old || old.used_traffic_bytes !== user.used_traffic_bytes || old.traffic_limit_bytes !== user.traffic_limit_bytes) {
        var caption = usageText(user), percent = user.quota_used_percent;
        if (percent == null && user.traffic_limit_bytes) percent = user.used_traffic_bytes * 100 / user.traffic_limit_bytes;
        M.mount(entry.usage, [h("span", { class: "usage-text", text: caption }), user.traffic_limit_bytes
          ? M.meter(percent, caption, percent >= 100 ? "danger" : percent >= 80 ? "warn" : "success") : null]);
      }
      var limited = user.status === "limited" || user.quota_used_percent >= 100 || user.quota_used_percent == null
        && user.traffic_limit_bytes > 0 && user.used_traffic_bytes >= user.traffic_limit_bytes;
      var attention = (user.attention_reasons || []).filter(function (key) { return key !== "quota" || !limited; });
      var reasons = attention.join(",");
      if (reasons !== entry.reasons) M.mount(entry.reasonSlot, attention.map(function (key) { return h("span", { class: "person-reason", text: M.t("attention.reason." + key) }); }));
      entry.reasons = reasons; entry.user = user;
    }
    function makeEntry(user) {
      var entry = { label: h("bdi", { class: "name" }), tags: h("span", { class: "people-tags" }), expiry: h("bdi", { class: "people-expiry" }),
        activity: h("bdi"), status: h("div", { class: "people-status" }), usage: h("div", { class: "people-usage" }), reasonSlot: h("span", { class: "people-reasons" }),
        declared: h("span", { class: "tag", "data-people-declared": "", title: M.t("people.declared_hint"), text: M.t("people.declared") }),
        notes: h("span", { "data-people-note": "", text: M.t("people.notes_present") }),
        select: h("input", { type: "checkbox", "data-person": user.username }) };
      entry.row = h("a", { class: "row row-link people-row", href: "#/people/" + encodeURIComponent(user.username) },
        h("div", { class: "row-title people-identity" }, entry.label, entry.tags), entry.status, entry.usage, entry.expiry,
        h("div", { class: "people-secondary muted small" }, entry.activity, entry.notes, entry.declared, entry.reasonSlot));
      entry.selection = h("label", { class: "people-select" }, entry.select);
      entry.wrapper = h("div", { class: "people-item" }, entry.selection, entry.row);
      entry.select.addEventListener("change", function () {
        if (entry.select.checked) selected.add(user.username); else selected.delete(user.username);
        lastSelected = entry.user.username; selectionChanged();
      });
      entry.select.addEventListener("keydown", function (event) {
        if (event.key !== " " || !event.shiftKey || lastSelected === null || matching) return;
        event.preventDefault();
        var index = ordered.indexOf(entry), anchor = ordered.findIndex(function (item) { return item.user.username === lastSelected; });
        if (anchor < 0) return;
        ordered.slice(Math.min(index, anchor), Math.max(index, anchor) + 1).forEach(function (item) { selected.add(item.user.username); item.select.checked = true; });
        selectionChanged();
      });
      update(entry, user); return entry;
    }
    function options(input, values, counts, label) {
      values.forEach(function (key, index) {
        var option = Array.from(input.options).find(function (item) { return item.value === key; }) || h("option", { value: key });
        if (input.options[index] !== option) input.insertBefore(option, input.options[index] || null);
        text(option, M.t("people.option_count", { name: label(key), count: number(M.has(counts, key) ? counts[key] : 0) }));
      });
      Array.from(input.options).forEach(function (option) { if (values.indexOf(option.value) < 0) option.remove(); });
      input.value = state[input.name];
    }
    function draw(result) {
      page = result; ready = true; loading.remove();
      var focus = document.activeElement, current = new Set();
      ordered = (result.users || []).map(function (user) {
        current.add(user.username);
        var entry = entries.get(user.username);
        if (!entry) { entry = makeEntry(user); entries.set(user.username, entry); } else update(entry, user);
        list.insertBefore(entry.wrapper, empty); return entry;
      });
      entries.forEach(function (entry, name) { if (!current.has(name)) entry.wrapper.remove(); });
      // Cache a few detached rows for filter changes, never the whole population.
      entries.forEach(function (entry, name) { if (entries.size > 500 && !current.has(name)) entries.delete(name); });
      if (focus && list.contains(focus)) focus.focus({ preventScroll: true });
      var facets = result.facets;
      options(controls.status, [""].concat(STATUSES), facets.status, function (key) { return key ? M.t("status." + key) : M.t("people.any_status"); });
      var tags = Object.keys(facets.tag).filter(function (key) { return key && key !== "none"; });
      if (state.tag && state.tag !== "none" && tags.indexOf(state.tag) < 0) tags.push(state.tag);
      options(controls.tag, ["", "none"].concat(tags.sort()), facets.tag, function (key) { return key === "" ? M.t("people.any_tag") : key === "none" ? M.t("people.no_tag") : key; });
      var profileCounts = facets.profile || {}, profiles = Object.keys(profileCounts).filter(function (key) { return key && key !== "default"; });
      if (state.profile && state.profile !== "default" && profiles.indexOf(state.profile) < 0) profiles.push(state.profile);
      options(controls.profile, ["", "default"].concat(profiles.sort()), profileCounts, profileLabel);
      options(controls.attention, ["", "all"].concat(REASONS), facets.attention, function (key) { return key ? M.t(key === "all" ? "people.attention" : "attention.reason." + key) : M.t("people.any_attention"); });
      options(controls.lifecycle, [""].concat(LIFECYCLES), facets.lifecycle, function (key) { return M.t("people.lifecycle_" + (key || "any")); });
      ["expires", "quota", "never"].forEach(function (key) { text(counters[key], M.t("people.filter_count", { count: number(facets[key]) })); });
      text(count, M.t("people.page_count", { shown: number(ordered.length), matching: number(result.total), total: number(result.population) }));
      if (!ordered.length) {
        text(emptyTitle, M.t(result.population ? result.total ? "people.page_empty" : "people.no_matches_title" : "people.empty_title"));
        text(emptyCaption, result.population ? result.total ? "" : M.t("people.no_filter_match") : M.t("people.empty_hint"));
      }
      emptyAdd.hidden = !!result.population; emptyClear.hidden = !result.population || !!result.total;
      empty.hidden = ordered.length > 0;
      previousButton.disabled = !previous.length; nextButton.disabled = !result.next_cursor; selectionChanged();
    }
    stop = M.liveView(view, { status: status, interval: 30000,
      load: function (passive) {
        var key = JSON.stringify(query()), requestedCursor = cursor;
        if (!passive || !page) {
          previousButton.disabled = nextButton.disabled = true;
          ready = false; selectionChanged();
        }
        var params = new URLSearchParams();
        Object.entries(query()).forEach(function (item) { if (item[1] !== null) params.set(item[0], item[0] === "never" ? item[1] ? "1" : "0" : String(item[1])); });
        if (requestedCursor) params.set("cursor", requestedCursor);
        return M.api("GET", "api/people?" + params.toString(), undefined, { passive: passive }).catch(function (error) {
          if (!requestedCursor || error.code !== "invalid_request" || key !== JSON.stringify(query()) || cursor !== requestedCursor) throw error;
          cursor = requestedCursor = null; previous = []; params.delete("cursor");
          return M.api("GET", "api/people?" + params.toString(), undefined, { passive: passive });
        }).then(function (result) { return { page: result, query: key, cursor: requestedCursor }; }).catch(function (error) {
          if (key === JSON.stringify(query()) && cursor === requestedCursor) {
            if (pageQuery === key) { cursor = pageCursor; previous = pagePrevious.slice(); }
            ready = !!page;
            previousButton.disabled = !previous.length; nextButton.disabled = !page || !page.next_cursor;
            selectionChanged();
          }
          throw error;
        });
      }, accept: function (result) { return result.query === JSON.stringify(query()) && result.cursor === cursor; },
      draw: function (result) { pageCursor = result.cursor; pagePrevious = previous.slice(); pageQuery = result.query; draw(result.page); }, empty: function () { loading.remove(); } });
    function modeChanged() {
      if (M.mode() === "simple" && (matching || selected.size)) selecting = true;
      applySelectionMode();
    }
    function outside(event) {
      Object.keys(filterMenus).forEach(function (name) { if (!filterMenus[name].contains(event.target)) filterMenus[name].open = false; });
      if (!event.target.closest(".people-bulk-more")) bulk.close();
    }
    document.addEventListener("click", outside);
    window.addEventListener("meridian:mode", modeChanged);
    return function () {
      clearTimeout(searchTimer); stop();
      document.removeEventListener("click", outside);
      window.removeEventListener("meridian:mode", modeChanged);
    };
  };
})();
