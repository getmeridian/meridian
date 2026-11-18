/* Edit only the owner's chosen fields, against the settings version they actually reviewed. */
(function () {
  "use strict";
  var M = window.M, h = M.h;
  function spec(key, label, attrs, hint, options) {
    return { key: key, label: M.t(label), attrs: attrs, hint: hint && M.t(hint), options: options };
  }
  function fields(server) {
    if (server) return [
      spec("provider", "server_allowance.provider", { maxlength: 80, dir: "auto" }),
      spec("allowance_bytes", "presentation.allowance_gib", { type: "text", inputmode: "decimal" }, "presentation.empty_allowance"),
      spec("reset_day", "server_allowance.reset_day", { type: "number", min: 1, max: 31, required: true }),
      spec("pay_on", "server_allowance.payment_anchor", { type: "date" }),
      spec("pay_every", "server_allowance.repeat", {}, null, [
        ["", "server_allowance.one_off"], ["month", "server_allowance.month"], ["year", "server_allowance.year"]]),
    ];
    return [
      spec("profile_title", "settings.title", { maxlength: 100, dir: "auto", required: true }, "settings.title_caption"),
      spec("update_interval_hours", "presentation.interval", { type: "number", min: 1, max: 168, required: true }),
      spec("text", "presentation.contact_text", { maxlength: 120, dir: "auto" }),
      spec("link", "presentation.contact_link", { maxlength: 2048, dir: "ltr", type: "text" }, "presentation.link_hint"),
    ];
  }
  function inputValue(key, value) {
    return key === "allowance_bytes" ? M.gbText(value) : value == null ? "" : String(value);
  }
  function display(field, value) {
    if (value == null || value === "") return M.t("presentation.unset");
    if (field.options) return M.t(field.options.find(function (option) { return option[0] === value; })[1]);
    return inputValue(field.key, value);
  }
  function review(rows) {
    return M.dialog(M.t("presentation.review"), function (close) {
      return [
        h("dl", { class: "settings-facts" }, rows.map(function (row) {
          return M.keyValue(row.label, h("div", { class: "stack-sm" },
            h("div", { class: "muted small" }, M.t("presentation.before"), " ", h("bdi", { text: row.before })),
            h("strong", {}, h("bdi", { text: row.after }))));
        })),
        h("div", { class: "actions" },
          h("button", { type: "button", class: "btn btn-ghost", onclick: function () { close(false); } }, M.t("action.cancel")),
          h("button", { type: "button", class: "btn btn-primary", onclick: function () { close(true); } }, M.t("action.save"))),
      ];
    });
  }
  M.editPresentation = function (server) {
    return M.dialog(M.t(server ? "presentation.billing_edit" : "presentation.edit"), function (close) {
      var base, version, defs = fields(server), fieldset = h("div", { class: "stack" });
      var error = h("p", { class: "form-error", role: "alert" });
      var submit = h("button", { type: "submit", class: "btn btn-primary", disabled: true }, M.t("presentation.review"));
      var reload = h("button", { type: "button", class: "btn btn-ghost", hidden: true, onclick: function () {
        if (!base || !M.formDirty(form)) return load();
        M.confirm(M.t("presentation.reload"), M.t("presentation.discard"), M.t("presentation.reload"), false)
          .then(function (ok) { if (ok) load(); });
      } }, M.t("presentation.reload"));
      var form = h("form", { class: "stack" }, server ? h("bdi", { class: "muted mono", text: server }) : null,
        fieldset, error, h("div", { class: "actions" }, reload, submit));
      defs.forEach(function (f) {
        f.input = f.options ? h("select", f.attrs, f.options.map(function (o) {
          return h("option", { value: o[0], text: M.t(o[1]) });
        })) : h("input", Object.assign({ type: "text", autocomplete: "off" }, f.attrs));
        f.input.disabled = true;
        f.input.addEventListener("input", function () { f.input.setCustomValidity(""); });
        fieldset.appendChild(M.field(f.label, f.input, f.hint));
      });
      function load() {
        submit.disabled = true; reload.hidden = true; error.textContent = "";
        return M.api("GET", "api/settings/presentation").then(function (state) {
          if (!form.isConnected) return;
          if (server && !M.has(state.servers, server)) throw new M.ApiError(400, "unknown_server", "");
          base = server ? state.servers[server] : Object.assign({}, state, state.owner_contact);
          version = state.version;
          defs.forEach(function (f) {
            f.input.value = f.initial = inputValue(f.key, base[f.key]);
            f.input.disabled = false; f.input.setCustomValidity("");
          });
          submit.disabled = false; M.trackForm(form); M.resetDraft(form); defs[0].input.focus();
        }).catch(function (e) {
          error.textContent = M.errorText(e); reload.hidden = false;
        });
      }
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        if (!base || submit.disabled || !form.reportValidity()) return;
        var patch = {}, rows = [], contact = {}, payment = null;
        for (var i = 0; i < defs.length; i++) {
          var f = defs[i], key = f.key, raw = f.input.value.trim(), value = raw;
          if (key === "pay_on") payment = raw;
          if (raw === f.initial) continue;
          if (key === "allowance_bytes" && raw) {
            var parsed = M.parseQuotaBytes(raw, "GiB");
            if (parsed.error) { f.input.setCustomValidity(M.t(parsed.error)); f.input.reportValidity(); return; }
            value = parsed.bytes;
          } else if (key === "reset_day" || key === "update_interval_hours") value = Number(raw);
          else if (!raw && ["allowance_bytes", "pay_on", "pay_every"].includes(key)) value = null;
          if (key === "text" || key === "link") contact[key] = value;
          else patch[key] = value;
          rows.push({ label: f.label, before: display(f, base[key]), after: display(f, value) });
        }
        if (server && !payment && defs[4].input.value) {
          defs[3].input.setCustomValidity(M.t("presentation.date_required")); defs[3].input.reportValidity(); return;
        }
        if (!rows.length) { close(true); return; }
        if (Object.keys(contact).length) patch.owner_contact = Object.assign({ text: "", link: "" }, base.owner_contact, contact);
        var request = { expected_version: version };
        if (server) { request.servers = Object.create(null); request.servers[server] = patch; }
        else Object.assign(request, patch);
        close.lock(true);
        M.saveDraft(form, function () {
          return review(rows).then(function (yes) {
            if (!yes) throw new M.ApiError(0, "cancelled", "");
            return M.api("PATCH", "api/settings/presentation", request);
          });
        }).then(function (state) {
          close.lock(false); close(state); M.toast(M.t(server ? "presentation.billing_saved" : "presentation.saved"));
        }, function (e) {
          close.lock(false);
          if (e.code === "stale_presentation") { reload.hidden = false; submit.disabled = true; }
        });
      });
      load();
      return form;
    });
  };
})();
