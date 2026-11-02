/* Each read row owns one inline draft; live facts refresh clean editors and preserve edits. */
(function () {
  "use strict";
  var M = window.M, h = M.h, sequence = 0;
  var owned = { expiry: ["expire_at", "activation_duration_days", "renewal_interval_days", "renewal_interval_months", "renewal_limit"],
    traffic: ["traffic_limit_bytes", "traffic_limit_strategy", "traffic_reset_anchor_day"],
    language: ["language"], note: ["note"], tag: ["tag"] };

  function date(value) {
    return value ? h("bdi", { tabindex: "0", title: value + " UTC", text: M.date(value) }) : M.t("people.no_expiry");
  }
  function read(key, user) {
    if (key === "expiry") {
      var schedule = user.schedule;
      var value = schedule ? schedule.expire_at : user.expire_at;
      return h("div", { class: "stack-sm" }, !value && user.activation_duration_days
        ? h("bdi", { text: M.firstUseText(user.activation_duration_days) }) : date(value),
        schedule && schedule.renewal_enabled ? h("span", { class: "muted small" },
          M.t("lifecycle.next_renewal"), " · ", schedule.next_renewal_at ? date(schedule.next_renewal_at)
            : M.t(schedule.activation_pending ? "lifecycle.waiting_first_use" : "lifecycle.no_renewals_left"),
          " · ", M.t("lifecycle.renewals_left"), " · ", h("bdi", { text: schedule.renewals_left === null
            ? M.t("lifecycle.unlimited_renewals") : M.count(schedule.renewals_left) })) : null);
    }
    if (key === "traffic") return user.traffic_limit_bytes ? M.t("person.traffic_allowance", {
      amount: M.bytes(user.traffic_limit_bytes), period: M.t("reset." + user.traffic_limit_strategy),
    }) : M.t("people.unlimited");
    if (key === "language") return user.language ? M.i18n.names[user.language] : M.t("language.person_default");
    if (key === "note") return user.note || M.t("person.no_note");
    return user.tag ? h("bdi", { dir: "ltr", text: user.tag }) : M.t("person.no_tag");
  }

  function textFields(key, user) {
    var original = user[key] || "";
    var input = key === "note" ? h("textarea", { name: "note", rows: "4" }, original)
      : h("input", { name: "tag", type: "text", value: original, dir: "ltr", autocomplete: "off" });
    var error = h("p", { class: "field-error", role: "alert" });
    function validate() {
      var message = "";
      if (key === "note") {
        var characters = Array.from(input.value);
        var invalid = characters.some(function (character) {
          var code = character.codePointAt(0);
          return code !== 10 && (code < 32 || code >= 127 && code <= 159 || code >= 55296 && code <= 57343);
        });
        if (characters.length > 500 || invalid) message = M.t("person.note_invalid");
      } else {
        var upper = input.value.toUpperCase(), caret = input.selectionStart;
        if (upper !== input.value) {
          input.value = upper;
          if (caret !== null && upper.length === Array.from(upper).length) input.setSelectionRange(caret, caret);
        }
        if (!/^[A-Z0-9_]{0,16}$/.test(input.value)) message = M.t("person.tag_invalid");
      }
      input.setCustomValidity(message); input.setAttribute("aria-invalid", String(!!message)); error.textContent = message;
      return !message;
    }
    input.addEventListener("input", validate);
    return { nodes: [M.field(M.t(key === "note" ? "person.note" : "person.tag"), input,
      M.t(key === "note" ? "person.note_hint" : "person.tag_hint")), error], value: function () {
      if (!validate()) { input.focus(); throw new Error(error.textContent); }
      var body = {}; if (input.value !== original) body[key] = input.value; return body;
    } };
  }

  function fields(key, user, currentUser) {
    if (key === "note" || key === "tag") return textFields(key, user);
    if (key === "language") {
      var original = user.language || "default";
      var language = h("select", { name: "language" }, h("option", { value: "default", text: M.t("language.person_default") }),
        M.i18n.langs.map(function (code) { return h("option", { value: code, lang: code, text: M.i18n.names[code] }); }));
      language.value = original;
      return { nodes: M.field(M.t("language.person"), language, M.t("language.person_hint")), value: function () {
        if (language.value !== "default" && M.i18n.langs.indexOf(language.value) < 0) throw new Error(M.t("error.invalid_request"));
        return language.value === original ? {} : { language: language.value === "default" ? null : language.value };
      } };
    }
    if (key === "expiry") {
      var expiry = M.expiryFields(user.expire_at, user.username, { inline: true });
      var strategy = h("select", {}, h("option", { value: user.traffic_limit_strategy }));
      var lifecycle = M.lifecycleFields(user, expiry, { strategy: strategy }, { inline: true, anchor: false });
      var presetTurn = 0;
      ["input", "change"].forEach(function (name) {
        lifecycle.nodes.addEventListener(name, function () { presetTurn++; });
      });
      function preset(months) {
        var account = currentUser();
        if (account.expire_at || !account.activation_duration_days) return expiry.preset(months);
        var mine = ++presetTurn;
        // The existing service extension review keeps pending first-use access pending.
        return M.api("POST", "api/people/bulk/preview", { usernames: [account.username], action: { action: "extend", months: months } })
          .then(function (review) {
            if (!lifecycle.nodes.isConnected || mine !== presetTurn) return;
            var latest = currentUser();
            if (latest.expire_at !== account.expire_at || latest.activation_duration_days !== account.activation_duration_days) return;
            var after = review.people[0].after;
            if (!after || !Number.isSafeInteger(after.activation_duration_days)) throw new M.ApiError(0, "invalid_request", "");
            lifecycle.setDuration(after.activation_duration_days);
          });
      }
      return { nodes: lifecycle.nodes, value: lifecycle.change, review: lifecycle.preview, preset: preset,
        setFixed: lifecycle.setFixed, stop: lifecycle.stop, refresh: lifecycle.refresh };
    }
    var limits = M.limitFields(user), anchor = M.resetAnchorFields(user, limits.strategy);
    function facts(schedule) { return M.scheduleFacts(schedule, { compact: true, expiry: false }); }
    var preview = h("div", { class: "lifecycle-preview", "aria-live": "polite" }, facts(user.schedule));
    var error = h("p", { class: "field-error schedule-error", role: "alert" });
    var nodes = h("div", { class: "stack-sm traffic-fields" }, limits.nodes, anchor.node, error, preview);
    function value() { return Object.assign(limits.value(), anchor.change()); }
    var schedulePreview = M.scheduleEditorPreview(nodes, preview, function () {
      return Object.assign({ username: user.username }, value());
    }, facts, error);
    limits.strategy.addEventListener("change", schedulePreview.update); anchor.input.addEventListener("input", schedulePreview.update);
    return { nodes: nodes, value: value, stop: schedulePreview.stop, refresh: function (changed, dirty, active) {
      if (dirty && active) schedulePreview.update(); else if (!dirty) schedulePreview.refresh(changed.schedule);
    }, review: function (change) {
      return schedulePreview.review(Object.assign({ username: user.username }, change));
    } };
  }

  M.personAccess = function (initial, saved, current) {
    var user = initial, entries = {}, prefix = "person-edit-" + (++sequence);
    var labels = { expiry: "people.expiry", traffic: "person.traffic", language: "person.page_language", note: "person.note", tag: "person.tag" };
    var facts = h("dl", { class: "person-access-facts" }), metadata = h("dl", { class: "person-access-facts" });
    function close(entry, focus) {
      if (!entry.form) return;
      if (entry.fields.stop) entry.fields.stop();
      M.discardDrafts(entry.form); M.clear(entry.editor); entry.form = null; entry.fields = null; entry.source = null;
      entry.edit.setAttribute("aria-expanded", "false");
      if (focus) entry.edit.focus();
    }
    function open(key, months, fixed, focus) {
      var entry = entries[key];
      if (!entry || entry.form && entry.form.meridianSaving) return Promise.resolve();
      var existing = entry.form;
      if (entry.row.closest("details")) entry.row.closest("details").open = true;
      if (!entry.form) {
        var controls = fields(key, user, function () { return user; });
        if (fixed && controls.setFixed) controls.setFixed(true);
        var form = h("form", { class: "stack person-inline-editor", novalidate: true, "aria-labelledby": entry.labelId },
          controls.nodes, h("p", { class: "form-error", role: "alert" }),
          h("div", { class: "row-actions" }, h("button", { class: "btn btn-primary btn-sm", type: "submit" }, M.t("action.save")),
            h("button", { class: "btn btn-ghost btn-sm", type: "button", "data-person-cancel": "", onclick: function () {
              M.tryLeave(form).then(function (leave) { if (leave && entry.form === form) close(entry, true); });
            } }, M.t("action.cancel"))));
        entry.form = form; entry.fields = controls; entry.source = user; M.mount(entry.editor, form); M.trackForm(form);
        ["input", "change"].forEach(function (name) {
          form.addEventListener(name, function () { if (!form.meridianSaving) form.querySelector(".form-error").textContent = ""; });
        });
        entry.edit.setAttribute("aria-expanded", "true");
        form.addEventListener("submit", function (event) {
          event.preventDefault();
          if (form.meridianSaving) return;
          var change;
          try { change = controls.value(); } catch (error) { form.querySelector(".form-error").textContent = error.message; return; }
          if (!Object.keys(change).length) { close(entry, true); M.toast(M.t("person.nothing_changed")); return; }
          var label = M.displayName(user.username), username = user.username;
          M.saveDraft(form, function () {
            return Promise.resolve(controls.review ? controls.review(change) : null).then(function () {
              return M.api("PATCH", "api/users/" + encodeURIComponent(username), change);
            });
          }).then(function (changed) { saved(changed, form); }, function (error) {
            if (!current() && error.code !== "cancelled") M.toast(M.t("person.action_error", { name: label, error: M.errorText(error) }), "error");
          });
        });
      }
      if (fixed && existing && entry.fields.setFixed) {
        var clean = !M.formDirty(entry.form);
        entry.fields.setFixed(true); if (clean) M.resetDraft(entry.form);
      }
      var first = entry.form.querySelector(fixed ? "input[type=date]" : "input:not(:disabled), select:not(:disabled), textarea");
      if (first && focus !== false) first.focus();
      return months && entry.fields.preset ? M.busy(entry.form, function () { return entry.fields.preset(months); }) : Promise.resolve();
    }
    Object.keys(labels).forEach(function (key) {
      var label = M.t(labels[key]), value = h("div", { class: "person-read-value" }), editor = h("div", { id: prefix + "-" + key });
      var edit = h("button", { class: "btn btn-ghost btn-sm", type: "button", "data-person-edit": key,
        "aria-label": M.t("person.edit_field", { field: label }), "aria-controls": editor.id, "aria-expanded": "false",
        onclick: function () { open(key); } }, M.t("person.edit"));
      var row = M.keyValue(label, [h("div", { class: "person-read-row" }, value, edit), editor]);
      row.classList.add("person-access-row"); row.setAttribute("data-person-field", key);
      row.querySelector("dt").id = prefix + "-" + key + "-label";
      entries[key] = { row: row, value: value, editor: editor, edit: edit, labelId: row.querySelector("dt").id };
      (key === "note" || key === "tag" ? metadata : facts).appendChild(row);
    });
    var more = h("details", { class: "secondary-actions person-more", "data-person-more": "" },
      h("summary", {}, M.t("person.more"), h("span", { class: "muted small", text: " · " + M.t("person.more_hint") })),
      h("div", { class: "stack-sm" }, metadata, h("p", { class: "hint", text: M.t("person.metadata_caption") }),
        h("div", { class: "row-actions" }, ["devices", "keys", "usage", "routing"].map(function (tab) {
          return h("a", { class: "btn btn-ghost btn-sm", href: M.personHash(user.username, tab), text: M.t(
            { devices: "person.tab_devices", keys: "person.tab_keys", usage: "person.tab_usage", routing: "person.tab_routing" }[tab]) });
        }))));
    var node = h("section", { class: "card person-access" }, h("h2", { text: M.t("person.tab_access") }), facts, more);
    function mode() {
      var focused = document.activeElement, selection = focused && typeof focused.selectionStart === "number"
        ? [focused.selectionStart, focused.selectionEnd] : null;
      var parent = M.mode() === "detailed" ? facts : metadata;
      [entries.note.row, entries.tag.row].forEach(function (row) { if (row.parentNode !== parent) parent.appendChild(row); });
      more.hidden = M.mode() === "detailed";
      if (!more.hidden && M.hasUnsavedEdits(metadata)) more.open = true;
      if (focused && node.contains(focused) && document.activeElement !== focused) {
        focused.focus(); if (selection) focused.setSelectionRange(selection[0], selection[1]);
      }
    }
    function refresh(changed, savedForm) {
      user = changed;
      Object.keys(entries).forEach(function (key) {
        var entry = entries[key]; M.mount(entry.value, read(key, user));
        if (entry.form && entry.form === savedForm) close(entry, entry.form.contains(document.activeElement));
        else if (entry.form && !entry.form.meridianSaving) {
          var dirty = M.formDirty(entry.form);
          if (!dirty && owned[key].some(function (field) { return entry.source[field] !== user[field]; })) {
            var focused = document.activeElement;
            var index = Array.from(entry.form.querySelectorAll("input, select, textarea, button")).indexOf(focused);
            var selection = index >= 0 && typeof focused.selectionStart === "number" ? [focused.selectionStart, focused.selectionEnd] : null;
            close(entry, false); open(key, null, false, false);
            if (index >= 0) {
              var next = entry.form.querySelectorAll("input, select, textarea, button")[index];
              if (!next || next.disabled) next = entry.form.querySelector("input:not(:disabled), select:not(:disabled), textarea");
              if (next) { next.focus(); if (selection && typeof next.selectionStart === "number") next.setSelectionRange(selection[0], selection[1]); }
            }
          } else if (entry.fields.refresh) entry.fields.refresh(user, dirty, !!savedForm);
        }
      });
      mode();
    }
    refresh(user);
    return { node: node, open: open, refresh: refresh, mode: mode, reset: function () {
      Object.keys(entries).forEach(function (key) { close(entries[key], false); });
    } };
  };
})();
